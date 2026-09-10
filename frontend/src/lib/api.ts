// Same-origin: Vite proxies /api to the backend in dev, Caddy does it in prod.
// Must NOT be an absolute cross-origin URL — a SameSite=Lax session cookie would
// never be sent with it. See the comment in vite.config.ts.
const BASE = ''

/** Read a non-httpOnly cookie. Used for the double-submit CSRF token. */
function cookie(name: string): string | null {
  const hit = document.cookie
    .split('; ')
    .find((c) => c.startsWith(name + '='))
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null
}

/** Raised on 401 so callers can distinguish "log in" from a real failure. */
export class Unauthorized extends Error {}

/**
 * A non-2xx response, carrying the status.
 *
 * The status is what lets a caller tell a refusal from a blip. A 403 is a
 * decision the server has already made and will make again; retrying it just
 * hides the failure behind seconds of backoff while the page shows zeros.
 */
export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// Shown when the response carries nothing written for a person -- a bare
// "Internal Server Error", a proxy's HTML error page, an empty body.
const REFUSAL: Record<number, string> = {
  401: 'Your session has expired. Sign in again.',
  403: 'You do not have permission to do that.',
  404: 'That record no longer exists.',
  409: 'That conflicts with something already saved.',
}

/**
 * Turn an error response into a sentence.
 *
 * Our own HTTPExceptions send `{"detail": "a contact needs at least a phone or an
 * email"}` -- already written for a person, so it is used verbatim. Schema
 * validation sends `{"detail": [{loc, msg}]}`, which has to be assembled. Anything
 * else has nothing worth showing, and printing the wire body is how a user ended up
 * reading `500 Internal Server Error` in a dialog.
 */
function readable(status: number, body: string): string {
  let detail: unknown
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail
  } catch {
    // Not a FastAPI error body -- fall through to the generic message.
  }
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d) => {
        const { loc, msg } = d as { loc?: unknown[]; msg?: string }
        const field = (loc ?? []).filter((p) => p !== 'body').join('.')
        const text = (msg ?? '').replace(/^Value error, /, '')
        return field ? `${field}: ${text}` : text
      })
      .join('; ')
  }
  return REFUSAL[status] ?? `Something went wrong (${status}).`
}

async function failed(r: Response): Promise<ApiError> {
  return new ApiError(r.status, readable(r.status, await r.text()))
}

let refreshing: Promise<boolean> | null = null

/**
 * Try once to renew the 15-minute access token.
 *
 * Single-flight: a page with several queries in flight will get several 401s at
 * once, and firing a refresh per query would rotate the refresh cookie
 * concurrently and log the user out.
 *
 * Exported so auth.ts's `me()` shares the SAME in-flight promise. It must not
 * run a second, competing refresh -- that is the concurrent rotation this guard
 * exists to prevent.
 */
export async function refresh(): Promise<boolean> {
  // No CSRF cookie means there is no session to renew: the browser is simply
  // signed out. Calling /api/auth/refresh anyway just earns a 403 -- visible in
  // the access log as a me->refresh 401/403 pair on every attempt while sitting
  // on the login screen. Nothing to renew, so do not ask.
  if (!cookie('ghl_csrf')) return false
  if (!refreshing) {
    refreshing = fetch(BASE + '/api/auth/refresh', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': cookie('ghl_csrf') ?? '' },
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        // Let the next 401 start a fresh attempt.
        setTimeout(() => (refreshing = null), 0)
      })
  }
  return refreshing
}

export type Contact = {
  id: number
  tags: string[]
  last_activity: string | null
  name: string
  first_name: string
  last_name: string
  email: string | null
  phone: string | null
  business_name: string | null
  created_at: string
}

export type Page<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

async function get<T>(path: string, retry = true): Promise<T> {
  const r = await fetch(BASE + path, { credentials: 'include' })
  if (r.status === 401) {
    // The access token lasts 15 minutes; renew it once and retry rather than
    // bouncing the user to the login screen mid-session.
    if (retry && (await refresh())) return get<T>(path, false)
    window.dispatchEvent(new Event('ghl:unauthorized'))
    throw new Unauthorized('not signed in')
  }
  if (!r.ok) throw await failed(r)
  return r.json() as Promise<T>
}

export function listContacts(params: {
  page: number
  page_size: number
  q?: string
  sort?: string
  order?: 'asc' | 'desc'
}) {
  const sp = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.page_size),
  })
  if (params.q) sp.set('q', params.q)
  if (params.sort) sp.set('sort', params.sort)
  if (params.order) sp.set('order', params.order)
  return get<Page<Contact>>(`/api/contacts?${sp}`)
}

export type Stage = {
  id: number
  name: string
  position: number
  count: number
  value_cents: number
}
export type Pipeline = { id: number; name: string; stages: Stage[] }

export const listPipelines = () => get<Pipeline[]>('/api/pipelines')

export type Opportunity = {
  id: number
  title: string
  value_cents: number
  stage_id: number
  // Rank within the stage, 0-based and packed. The board needs it to work out
  // where a card dropped in a FILTERED column belongs among all of the stage's
  // cards -- see lib/boardOrder.ts.
  position: number
  status: string
  contact_name: string | null
  business_name: string | null
  source: string | null
  updated_at: string
}

export const listOpportunities = (pipelineId: number, q = '', status = 'open') => {
  const sp = new URLSearchParams({ pipeline_id: String(pipelineId), status })
  if (q) sp.set('q', q)
  return get<Opportunity[]>(`/api/opportunities?${sp}`)
}

export type OpportunityMove = { id: number; stage_id: number; position: number }

// A kanban drag is a cookie-authenticated write like any other, so it goes through
// send(). A bare fetch() omits the double-submit CSRF header and the backend rejects
// every drag with 403 "csrf token missing or mismatched".
//
// `position` has no default any more. It had one, of 0, and the drag handler never
// passed anything, so every card dropped in another column landed at the top of it
// rather than where it was released. A caller that does not know where the card
// goes should not be able to leave it out.
export const moveOpportunity = (id: number, stageId: number, position: number) =>
  send<OpportunityMove>(`/api/opportunities/${id}`, 'PATCH', {
    stage_id: stageId,
    position,
  })

export type ConversationSummary = {
  id: number
  contact_id: number
  contact_name: string | null
  contact_phone: string | null
  last_event_at: string
  unread_count: number
  starred: boolean
}

export type ThreadEvent = {
  id: number
  type: string
  direction: 'INBOUND' | 'OUTBOUND'
  occurred_at: string
  body: string | null
  subject: string | null
  duration_seconds: number | null
  recording_url: string | null
  delivery_status: string | null
}

export const listConversations = (tab: string, sort: string) =>
  get<ConversationSummary[]>(
    `/api/conversations?tab=${encodeURIComponent(tab)}&sort=${encodeURIComponent(sort)}`,
  )

export const listEvents = (convId: number, filter: string) =>
  get<ThreadEvent[]>(
    `/api/conversations/${convId}/events?filter=${encodeURIComponent(filter)}`,
  )

export const money = (cents: number) =>
  (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })

/**
 * Dollars typed into a money input -> integer cents, without going through a float.
 *
 * `Math.round(Number(v) * 100)` looks equivalent and is not: 9500.005 * 100 is
 * 950000.49999999994 in IEEE-754, so the half rounds DOWN and the cent the user
 * typed disappears. Splitting the decimal string and rounding on the third digit
 * keeps the arithmetic in integers, where a cent cannot be invented or lost.
 *
 * Anything that is not a plain decimal (a number input can still hand back "1e3")
 * falls back to the float path rather than being silently read as zero.
 */
export function centsFromDollars(input: string): number {
  const text = input.trim()
  if (!text) return 0
  const m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(text)
  if (!m) {
    const n = Number(text)
    return Number.isFinite(n) ? Math.round(n * 100) : 0
  }
  const [, sign, whole, frac = ''] = m
  const cents =
    Number(whole || '0') * 100 +
    Number((frac + '00').slice(0, 2)) +
    // half-up, decided on the digit itself
    (frac.length > 2 && frac[2] >= '5' ? 1 : 0)
  return sign === '-' ? -cents : cents
}

export type User = { id: number; name: string; email: string; role: string }
export const listUsers = () => get<User[]>('/api/users')

export type Appointment = {
  id: number
  title: string
  starts_at: string
  ends_at: string
  status: string
  assigned_user_id: number | null
  calendar_id: number | null
  calendar_name: string | null
  /** The owning calendar's colour, or the default blue. Sent by the API already. */
  color: string
  contact_name: string | null
}

export function listAppointments(p: {
  start: string
  end: string
  kind: string
  user_ids: number[]
  calendar_ids?: number[]
  pipeline_ids?: number[]
}) {
  const sp = new URLSearchParams({ start: p.start, end: p.end, kind: p.kind })
  if (p.user_ids.length) sp.set('user_ids', p.user_ids.join(','))
  if (p.calendar_ids?.length) sp.set('calendar_ids', p.calendar_ids.join(','))
  if (p.pipeline_ids?.length) sp.set('pipeline_ids', p.pipeline_ids.join(','))
  return get<Appointment[]>(`/api/appointments?${sp}`)
}

/**
 * Create a booking. STAFF-only on the backend, so the callers gate on the role
 * first -- a TECH must not be able to fill this form and learn on submit.
 *
 * The body mirrors `AppointmentCreate` in backend/app/main.py exactly. Note what
 * is NOT there: `status`. The POST model does not accept it, so a booking always
 * lands on the model default (`confirmed`); adding it here would be a field the
 * server silently ignores.
 */
export type AppointmentCreated = {
  id: number
  title: string
  starts_at: string
  ends_at: string
  automation: string
}

export const createAppointment = (body: {
  title: string
  starts_at: string
  ends_at: string
  contact_id?: number | null
  assigned_user_id?: number | null
  calendar_id?: number | null
  notes?: string | null
}) => send<AppointmentCreated>('/api/appointments', 'POST', body)

/**
 * What `GET /api/appointments/{id}` returns -- everything the detail panel draws.
 * Wider than the `Appointment` rows the grid is built from: the grid never needs
 * `notes`, and the panel cannot edit a contact it only knows the name of.
 */
export type AppointmentDetail = {
  id: number
  title: string
  starts_at: string
  ends_at: string
  status: string
  notes: string | null
  contact_id: number | null
  contact_name: string | null
  calendar_id: number | null
  calendar_name: string | null
  assigned_user_id: number | null
}

export const getAppointment = (id: number) =>
  get<AppointmentDetail>(`/api/appointments/${id}`)

/**
 * Edit or reschedule. STAFF-only on the backend, so the panel gates its Save and
 * Cancel controls on the role first.
 *
 * The fields are `AppointmentPatch` in backend/app/main.py, and unlike the create
 * body this one DOES carry `status` -- the PATCH model accepts it, validated
 * against the measured status set. Send only what changed: the endpoint uses
 * `exclude_unset`, so an absent key leaves the stored value alone, and a
 * `starts_at` that is present but unchanged must not look like a reschedule.
 *
 * `automation` says what happened to the customer's reminders: "queued 24h,1h"
 * after a move, "reminders cancelled" when the booking was switched off, or
 * "unchanged" when the edit did not touch the time. Reminder jobs dedupe on a key
 * that includes the start time, so this is the field that says the reminder
 * followed the appointment (CLAUDE.md, and the tests in test_messaging.py).
 */
export type AppointmentPatch = {
  title?: string
  starts_at?: string
  ends_at?: string
  status?: string
  contact_id?: number | null
  calendar_id?: number | null
  assigned_user_id?: number | null
  notes?: string | null
}

export const patchAppointment = (id: number, body: AppointmentPatch) =>
  send<AppointmentDetail & { automation: string }>(
    `/api/appointments/${id}`, 'PATCH', body)

/**
 * Cancel a booking. This is a status change to `cancelled`, NOT a row delete --
 * GHL's own Appointment report has a Cancelled tile, so a cancelled booking is a
 * record and the calendar keeps drawing it, struck through.
 *
 * `reminders_cancelled` is how many pending reminders were retired with it. It is
 * returned rather than assumed because a reminder left in the queue for a
 * cancelled appointment is the failure mode this whole task exists around.
 */
export type AppointmentCancelled = {
  id: number
  status: string
  reminders_cancelled: number
}

export const cancelAppointment = (id: number) =>
  send<AppointmentCancelled>(`/api/appointments/${id}`, 'DELETE')

export type ContactTag = { id: number; name: string; color: string }

/** Enough to name an opportunity in the "these will be detached" confirmation. */
export type ContactOpportunity = { id: number; title: string }

export type ContactDetail = {
  id: number
  name: string
  first_name: string
  last_name: string
  email: string | null
  phone: string | null
  business_name: string | null
  source: string | null
  date_of_birth: string | null
  contact_type: string | null
  dnd: boolean
  created_by: string | null
  created_at: string
  owner_id: number | null
  owner_name: string | null
  tags: ContactTag[]
  opportunities: ContactOpportunity[]
  custom_fields: Record<string, unknown>
}

export const getContact = (id: number) => get<ContactDetail>(`/api/contacts/${id}`)

async function send<T>(path: string, method: string, body?: unknown,
                       retry = true): Promise<T> {
  const headers: Record<string, string> = {
    // Cookie auth is ambient, so every write carries the double-submit token.
    'X-CSRF-Token': cookie('ghl_csrf') ?? '',
  }
  if (body) headers['Content-Type'] = 'application/json'

  const r = await fetch(BASE + path, {
    method,
    credentials: 'include',
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (r.status === 401) {
    if (retry && (await refresh())) return send<T>(path, method, body, false)
    window.dispatchEvent(new Event('ghl:unauthorized'))
    throw new Unauthorized('not signed in')
  }
  if (!r.ok) throw await failed(r)
  return r.json() as Promise<T>
}

export const patchContact = (id: number, body: Partial<ContactDetail>) =>
  send<ContactDetail>(`/api/contacts/${id}`, 'PATCH', body)

export const addContactTag = (id: number, name: string) =>
  send<ContactDetail>(`/api/contacts/${id}/tags`, 'POST', { name })

export const removeContactTag = (id: number, tagId: number) =>
  send<ContactDetail>(`/api/contacts/${id}/tags/${tagId}`, 'DELETE')

export type ContactDeleted = { deleted: number; detached_opportunities: number[] }

/**
 * Delete a contact. ADMIN only, and 409 while it still has opportunities.
 *
 * `force` does NOT mean "delete the opportunities too" -- they are detached and
 * kept, because `custom_fields.owen_call_id` is the telephony project's join key
 * (DECISIONS.md). It means "yes, detach them". The conversations are deleted
 * either way.
 */
export const deleteContact = (id: number, force = false) =>
  send<ContactDeleted>(`/api/contacts/${id}${force ? '?force=true' : ''}`, 'DELETE')

export type OpportunityDetail = {
  id: number
  title: string
  pipeline_id: number
  stage_id: number
  status: string
  value_cents: number
  owner_id: number | null
  owner_name: string | null
  business_name: string | null
  source: string | null
  expected_close_date: string | null
  created_by: string | null
  created_at: string
  custom_fields: Record<string, unknown>
  contact_id: number | null
  contact_name: string | null
  contact_email: string | null
  contact_phone: string | null
}

export const getOpportunity = (id: number) =>
  get<OpportunityDetail>(`/api/opportunities/${id}`)

export const patchOpportunity = (id: number, body: Partial<OpportunityDetail>) =>
  send<OpportunityDetail>(`/api/opportunities/${id}/detail`, 'PATCH', body)

export const createOpportunity = (body: {
  title: string
  pipeline_id: number
  stage_id: number
  contact_id?: number | null
  value_cents: number
}) => send<{ id: number; title: string; stage_id: number }>(
  '/api/opportunities', 'POST', body)

export const createContact = (body: {
  first_name?: string
  last_name?: string
  phone?: string
  email?: string
  business_name?: string
  source?: string
}) => send<ContactDetail>('/api/contacts', 'POST', body)

// Measured GHL pane widths across 1440/1920/2560 (capture/capture_sizes.py):
//   sidebar 224 fixed | icon rail 52 fixed
//   inbox list 319 -> 379 -> 379   (grows, then caps)
//   thread     480 -> 826 -> 1299  (fluid remainder)
//   panel      299 -> 373 -> 540   (~21vw, capped)
export const PANE = {
  sidebar: 224,
  rail: 52,
  list: 'clamp(319px, 22vw, 379px)',
  panel: 'clamp(299px, 21vw, 540px)',
}

export type CalendarRow = {
  id: number
  name: string
  color: string
  user_id: number | null
  user_name: string | null
  pipeline_id: number | null
  pipeline_name: string | null
}
export const listCalendars = () => get<CalendarRow[]>('/api/calendars')

export type DashboardStats = {
  total: number
  status: Record<string, number>
  total_value_cents: number
  won_value_cents: number
  conversion_rate: number
}
export const getDashboard = (pipelineId?: number) =>
  get<DashboardStats>(`/api/dashboard${pipelineId ? `?pipeline_id=${pipelineId}` : ''}`)

export type CallReport = {
  total_calls: number
  by_status: Record<string, number>
  first_time_by_status: Record<string, number>
  avg_duration_seconds: number
  total_duration_seconds: number
  first_time_avg_duration_seconds: number
  first_time_total_duration_seconds: number
  top_sources: { source: string; calls: number; won: number; avg_duration: number }[]
}
export const getCallReport = (p: { start: string; end: string; direction: string }) =>
  get<CallReport>(`/api/reports/calls?start=${p.start}&end=${p.end}&direction=${p.direction}`)

export type AppointmentReport = {
  total: number
  tiles: Record<string, number>
  by_source: { source: string; count: number }[]
  by_calendar: { calendar: string; count: number }[]
  by_channel: { channel: string; count: number }[]
  outcomes: Record<string, number>
}
export function getAppointmentReport(p: {
  start: string; end: string; calendar_ids: number[]
}) {
  const sp = new URLSearchParams({ start: p.start, end: p.end })
  if (p.calendar_ids.length) sp.set('calendar_ids', p.calendar_ids.join(','))
  return get<AppointmentReport>(`/api/reports/appointments?${sp}`)
}
