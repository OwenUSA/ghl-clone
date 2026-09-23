import type { SearchResponse } from './search'

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
  /** E.164 when it parsed; otherwise exactly what was typed. */
  phone: string | null
  /** What a person should read. Falls back to the stored string. */
  phone_display: string | null
  /** Non-blocking: set when the number could not be parsed. It was still saved. */
  phone_warning: string | null
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
export type Pipeline = {
  id: number
  name: string
  stages: Stage[]
  /** On: each deal carries its own probability (the modal shows the field). Sent
      by GET /api/pipelines since feature/ghl-pipelines; optional so fixtures that
      predate it still type-check. */
  use_opportunity_probability?: boolean
}

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
  /** Which board it is filed on. Only interesting when listing across pipelines. */
  pipeline_id: number
  // ---- the card's icon row (2026-09-13) ----
  contact_id: number | null
  owner_id: number | null
  owner_name: string | null
  /** The PRIMARY CONTACT's tag names — an opportunity has no tags of its own. */
  tags: string[]
  open_tasks_count: number
  /** The Checklist tab's progress on THIS card's pipeline, or null when it asks no
      checklist question (2026-09-14). Optional so older fixtures type-check. */
  checklist?: { answered: number; total: number; group_id: number } | null
  /** The JOB's address (2026-09-14); the card draws "street, city" when set. */
  address_street?: string | null
  address_city?: string | null
  address_state?: string | null
  address_postal_code?: string | null
  /** STAFF only. The server leaves both keys OUT for a TECH, so `undefined`
      means "this role may not see notes", never "no notes". */
  notes_count?: number
  note_previews?: string[]
  /** Zuper v2 (2026-09-16): sent to Zuper — the card is a mirror and cannot be dragged. */
  managed_in_zuper?: boolean
}

export const listOpportunities = (pipelineId: number, q = '', status = 'open') => {
  const sp = new URLSearchParams({ pipeline_id: String(pipelineId), status })
  if (q) sp.set('q', q)
  return get<Opportunity[]>(`/api/opportunities?${sp}`)
}

/**
 * Every deal with this status, across every pipeline.
 *
 * `pipeline_id` is optional on the endpoint precisely so this can exist: the
 * appointment dialog offers "bind this booking to an open deal" and has no
 * business knowing, or asking, which board the deal is filed on.
 */
export const listOpenOpportunities = () =>
  get<Opportunity[]>('/api/opportunities?status=open')

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
  /** Unique within its KIND only — a contact thread and a number thread can share an
   *  id. Select and compare rows by `key`. */
  id: number
  /** "c<id>" for a contact thread, "n<id>" for a number-only thread. */
  key: string
  /** 'number' is a thread for a phone number no contact holds (2026-09-13). */
  kind: 'contact' | 'number'
  /** Null on a number-only thread: there is no contact, and nothing pretends so. */
  contact_id: number | null
  number_thread_id: number | null
  contact_name: string | null
  contact_phone: string | null
  phone_display: string | null
  /** The name Quo's own contact book has for the number. Shown "from Quo". */
  quo_name: string | null
  /** Do Not Disturb. The composer disables Send rather than let a message be typed
   *  and then silently suppressed. */
  contact_dnd: boolean
  last_event_at: string
  /** Unread MESSAGES on this one thread. The Unread tab's badge counts rows
   *  instead — see lib/inbox.ts. */
  unread_count: number
  /** Everything on the thread, unfiltered. The delete confirmation names it. */
  event_count: number
  starred: boolean
}

/**
 * One picture on a message.
 *
 * `url` is null until the bytes are actually here, which is what the thread branches on:
 * a PENDING picture is still being fetched from the phone system and shows a placeholder,
 * a FAILED one shows `detail` and a Retry, and a REFUSED one shows `detail` with no Retry
 * because trying again cannot make a PDF into a photograph.
 */
export type Attachment = {
  id: number
  position: number
  status: 'PENDING' | 'STORED' | 'FAILED' | 'REFUSED' | 'DRAFT'
  detail: string | null
  content_type: string | null
  byte_size: number | null
  filename: string | null
  url: string | null
  pending: boolean
  retryable: boolean
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
  /**
   * CALL only: completed | no-answer | busy | voicemail | failed, or null when the
   * outcome is not known yet (an outbound call we just placed) or was never sent.
   */
  call_status: string | null
  /**
   * QUEUED -> SENT -> DELIVERED, or FAILED. Plus two that are not on that ladder:
   * REFUSED (it never left, and we know why) and LOGGED_ONLY (recorded by the stub
   * transport, never transmitted). Null on inbound messages and on internal notes,
   * neither of which is delivered to anyone.
   */
  delivery_status: string | null
  /** The reason, as a sentence, when the status alone does not explain itself. */
  delivery_detail: string | null
  /**
   * The pictures on this message (2026-09-16). ALWAYS an array; empty on almost every
   * row. Each entry carries an id this server will serve the bytes for and nothing else —
   * no carrier URL, no owen-main locator, no path. See backend/app/message_media.py.
   */
  attachments: Attachment[]
  /**
   * WHICH phone system carried this event -- "BulkVS" for anything this CRM sent or
   * owen-main relayed, "OpenPhone" for an event mirrored from the account the company
   * is migrating away from.
   *
   * Null means "not recorded", and renders NO chip. Every row written before the
   * mirror existed has no observed source; labelling those "BulkVS" would be a guess
   * dressed up as a fact on a customer's record.
   */
  source_system: string | null
  /** The LINE it came through, e.g. "+19417247244". Null renders no chip. */
  source_number: string | null
  /** What was said on a call, when the far side transcribed it (Quo does). */
  transcript?: string | null
  /**
   * What an AI agent did on this call (2026-09-22). Null on every row a person, a feed
   * or an automation wrote, which is almost all of them.
   *
   * `captured` is what the agent heard and wrote down, NOT a customer record: a call on a
   * number-only thread stays a number-only thread until a person promotes it. The CRM
   * never treats a capture as a contact by itself.
   */
  ai_call?: AiCall | null
}

/** The agent's own record of a call it answered, as owen-main reported it. */
export type AiCall = {
  /** The agent's name as owen-main published it, e.g. "Roofing Receptionist". */
  agent?: string | null
  /** Its version number, so a bad answer can be traced back to a prompt. */
  version?: number | null
  /** How the conversation ended: end_call | transfer | default | failed. */
  outcome?: string | null
  /** The campaign that owns the line the call came in on. */
  campaign?: string | null
  /** name / phone / address / intent / urgency / notes - whatever the caller gave. */
  captured?: Record<string, string> | null
}

/**
 * The inbox list.
 *
 * `assigned` and `q` are the icon rail: "Assigned to me" / "Team inbox", and the
 * search that narrows the inbox in place. All four arguments narrow ONE query
 * and intersect, so the Unread badge and the list it labels are always computed
 * over the same set.
 */
export const listConversations = (
  tab: string, sort: string, assigned: 'all' | 'me' = 'all', q = '',
) => {
  // URLSearchParams, not template interpolation: a `+` or a `&` typed into the
  // search box would otherwise arrive as a different query than the one on
  // screen. (An ISO timestamp's `+00:00` decoding as a space is the same bug
  // this project has already been bitten by once.)
  const sp = new URLSearchParams({ tab, sort, assigned })
  if (q.trim()) sp.set('q', q.trim())
  return get<ConversationSummary[]>(`/api/conversations?${sp}`)
}

export const listEvents = (convId: number, filter: string) =>
  get<ThreadEvent[]>(
    `/api/conversations/${convId}/events?filter=${encodeURIComponent(filter)}`,
  )

/**
 * Mark a thread read/unread, or star it.
 *
 * The endpoint has accepted `{"read": true}` since the inbox was built and the
 * `ghl` CLI has used it all along; nothing in the browser ever called it, which
 * is why the blue badge never cleared.
 */
export const patchConversation = (
  convId: number, body: { read?: boolean; starred?: boolean },
) => send<ConversationSummary>(`/api/conversations/${convId}`, 'PATCH', body)

export type ConversationDeleted = {
  deleted: number
  contact_id: number
  events_deleted: number
}

/**
 * Delete a thread and its events. ADMIN only, and there is no undo — this
 * codebase has no soft delete (DECISIONS.md). The contact, their opportunities
 * and their appointments are untouched.
 */
export const deleteConversation = (convId: number) =>
  send<ConversationDeleted>(`/api/conversations/${convId}`, 'DELETE')

/** What the composer may write. Internal notes are STAFF-only (DECISIONS.md). */
export type SendableType = 'SMS' | 'INTERNAL_COMMENT'

export type SentMessage = {
  /**
   * True when OUR rules stopped the send before anything was attempted -- the
   * contact is on DND, or has no phone number. Nothing is written in that case, so
   * there is no row to show and `id` is null.
   */
  suppressed: boolean
  /** "queued" | "sent" | "recorded" | "refused: ..." | "suppressed: ..." */
  reason: string
  id: number | null
  conversation_id: number | null
  delivery_status?: string | null
  delivery_detail?: string | null
}

export const sendMessage = (convId: number, body: string, type: SendableType) =>
  send<SentMessage>(`/api/conversations/${convId}/messages`, 'POST', { body, type })

export type CallPlaced = {
  /** False for a refusal -- dark link, not allowlisted, DND, no number. */
  placed: boolean
  /** Always a sentence fit to show the operator, whether it worked or not. */
  reason: string
  id: number | null
}

/**
 * Ring the customer from the bound DID. There is no browser softphone: owen-main
 * rings an operator's handset first, then the customer, and bridges them.
 */
export const placeCall = (convId: number) =>
  send<CallPlaced>(`/api/conversations/${convId}/call`, 'POST')

/** Ring a contact from the bound DID — the same click-to-call path as the thread
    header, addressed by contact rather than by conversation (the board card). */
export const callContact = (contactId: number) =>
  send<CallPlaced>(`/api/contacts/${contactId}/call`, 'POST')

/** The contact's thread, created empty if there is none. Sends nothing. */
export const openContactConversation = (contactId: number) =>
  send<{ conversation_id: number; created: boolean }>(
    `/api/contacts/${contactId}/conversation`, 'POST')

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
  /** The deal this visit is for, or null. Both ends of the link are readable. */
  opportunity_id: number | null
  opportunity_title: string | null
  /** "Meeting location", resolved when it was booked. */
  location: string | null
  /** The deal's street and city ("5790 SW 34th St, Miami"), or null. Blanked with
   *  opportunity_title for a deal the reader cannot see. Absent from the detail
   *  endpoint's older callers, hence optional. */
  opportunity_address?: string | null
  /** The Workiz Job # the deal was imported from, or null. */
  workiz_job_id?: string | null
  /** Zuper v2: a visit of a job managed in Zuper — read-only here. */
  zuper_locked?: boolean
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
 * The body mirrors `AppointmentCreate` in backend/app/main.py exactly. `status`
 * joined it on 2026-09-13 with GoHighLevel's Book appointment modal, whose footer
 * books with a status. `assigned_user_id` stays in the contract for the CLI; the
 * modal leaves it out and the server assigns the calendar's user.
 *
 * A booking over blocked off time on the same calendar is refused 409 until it is
 * re-sent with `allow_blocked_time: true`, after the booker has confirmed.
 */
export type AppointmentCreated = {
  id: number
  title: string
  starts_at: string
  ends_at: string
  opportunity_id: number | null
  assigned_user_id: number | null
  status: string
  description: string | null
  location: string | null
  automation: string
}

export const createAppointment = (body: {
  /** May carry the contact.name template variable; the server resolves it. */
  title: string
  starts_at: string
  ends_at: string
  contact_id?: number | null
  assigned_user_id?: number | null
  calendar_id?: number | null
  /** Optional in both directions -- see NewAppointmentDialog. */
  opportunity_id?: number | null
  notes?: string | null
  description?: string | null
  location_kind?: 'calendar_default' | 'custom' | null
  location?: string | null
  status?: string
  allow_blocked_time?: boolean
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
  /** STAFF-only internal notes: null for a TECH, with `notes_visible` false. */
  notes: string | null
  notes_visible: boolean
  description: string | null
  location: string | null
  contact_id: number | null
  contact_name: string | null
  calendar_id: number | null
  calendar_name: string | null
  opportunity_id: number | null
  opportunity_title: string | null
  assigned_user_id: number | null
  /** Zuper v2: a visit of a job managed in Zuper — no edit, no cancel. */
  zuper_locked?: boolean
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
  /** Bind or unbind the deal. Does NOT reschedule anything -- only the time and
      the status touch the reminder queue. */
  opportunity_id?: number | null
  notes?: string | null
  description?: string | null
  location_kind?: 'calendar_default' | 'custom' | null
  location?: string | null
  allow_blocked_time?: boolean
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
  /** Always false. The route is a DELETE; the outcome is a status change. */
  deleted: false
  reminders_cancelled: number
}

export const cancelAppointment = (id: number) =>
  send<AppointmentCancelled>(`/api/appointments/${id}`, 'DELETE')

export type ContactTag = { id: number; name: string; color: string }

/** Enough to name an opportunity in the "these will be detached" confirmation. */
export type ContactOpportunity = { id: number; title: string }

/** The same, for the bookings that delete would sever from this customer. */
export type ContactAppointment = {
  id: number
  title: string
  starts_at: string
  status: string
}

export type ContactDetail = {
  id: number
  name: string
  first_name: string
  last_name: string
  email: string | null
  phone: string | null
  phone_display: string | null
  phone_warning: string | null
  business_name: string | null
  source: string | null
  /** Read-only property address (the Workiz import). "Calendar default" location. */
  address_street?: string | null
  address_city?: string | null
  address_state?: string | null
  address_postal_code?: string | null
  date_of_birth: string | null
  contact_type: string | null
  dnd: boolean
  created_by: string | null
  created_at: string
  owner_id: number | null
  owner_name: string | null
  tags: ContactTag[]
  opportunities: ContactOpportunity[]
  appointments: ContactAppointment[]
  custom_fields: Record<string, unknown>
  /** Zuper v2: the customer of a sent job — these fields are Zuper's. */
  zuper_locked?: boolean
  zuper_locked_fields?: string[]
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

export type ContactDeleted = {
  deleted: number
  detached_opportunities: number[]
  detached_appointments: number[]
  /** Pending reminders retired with the detached appointments. */
  reminders_cancelled: number
}

/**
 * Delete a contact. ADMIN only, and 409 while it still has opportunities or
 * appointments.
 *
 * `force` does NOT mean "delete those too" -- both are detached and kept. An
 * opportunity carries `custom_fields.owen_call_id`, the telephony project's join
 * key (DECISIONS.md); an appointment is the record that a slot was taken, the
 * same reason cancelling one keeps its row. `force` means "yes, detach them".
 * The conversations are deleted either way.
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
  /** The ANSWERS, keyed by a definition's `key`. The questions come from
      `listCustomFields`; this blob is what somebody typed. */
  custom_fields: Record<string, unknown>
  contact_id: number | null
  contact_name: string | null
  contact_email: string | null
  contact_phone: string | null
  /** The visits booked for this deal, soonest first. */
  appointments: LinkedAppointment[]
  /** The deal's own win probability, 0-100; only meaningful on a pipeline that
      uses opportunity-level probability. */
  probability: number | null
  /** The address of the job/property this card is for — not the contact's. */
  address_street: string | null
  address_city: string | null
  address_state: string | null
  address_postal_code: string | null
  followers: { id: number; name: string }[]
  additional_contacts: {
    id: number; name: string; email: string | null; phone: string | null
  }[]
  /** The primary contact's tags. */
  contact_tags: ContactTag[]
  /** The primary contact's thread, or null when there is none yet. */
  conversation_id: number | null
  /** Zuper v2 (2026-09-16): Send to Zuper and the mirror's locks. Optional so older fixtures
      type-check; absent reads as "nothing locked, no send control". */
  zuper?: import('./zuper').OpportunityZuper
  /** CRM-only: why a lead closed without booking (lib/zuper.ts LEAD_OUTCOMES). */
  lead_outcome?: string | null
  lead_outcome_note?: string | null
  /** A live visit, or sent to Zuper: closing it needs no lead outcome. */
  booked?: boolean
}

/** What the modal's Update sends. The two id lists REPLACE the set they name. */
export type OpportunityPatch = {
  title?: string
  contact_id?: number
  pipeline_id?: number
  stage_id?: number
  status?: string
  value_cents?: number
  owner_id?: number | null
  follower_ids?: number[]
  additional_contact_ids?: number[]
  business_name?: string | null
  source?: string | null
  expected_close_date?: string | null
  custom_fields?: Record<string, unknown>
  /** 0-100, or null to clear it. */
  probability?: number | null
  /** Zuper v2: required with status lost/abandoned on an unbooked card; Other needs the note. */
  lead_outcome?: string | null
  lead_outcome_note?: string | null
  address_street?: string | null
  address_city?: string | null
  address_state?: string | null
  address_postal_code?: string | null
}

export type OpportunityDeleted = {
  deleted: number
  detached_appointments: number[]
  notes_deleted: number
  tasks_deleted: number
  followers_removed: number
  additional_contacts_removed: number
}

/** ADMIN. Appointments are detached, never deleted; the deal's own notes and
    tasks go with it, and the response counts them. */
export const deleteOpportunity = (id: number) =>
  send<OpportunityDeleted>(`/api/opportunities/${id}`, 'DELETE')

// ---- tasks (2026-09-13). A task notifies nobody: no reminder, no text. ----

export type OpportunityTask = {
  id: number
  opportunity_id: number
  contact_id: number | null
  title: string
  description: string | null
  due_at: string | null
  assigned_user_id: number | null
  assigned_user_name: string | null
  done: boolean
  completed_at: string | null
  created_by_id: number | null
  created_at: string
}

export type TaskInput = {
  title?: string
  description?: string | null
  due_at?: string | null
  assigned_user_id?: number | null
  done?: boolean
}

export const listTasks = (oppId: number) =>
  get<OpportunityTask[]>(`/api/opportunities/${oppId}/tasks`)
export const createTask = (oppId: number, body: TaskInput) =>
  send<OpportunityTask>(`/api/opportunities/${oppId}/tasks`, 'POST', body)
export const patchTask = (id: number, body: TaskInput) =>
  send<OpportunityTask>(`/api/tasks/${id}`, 'PATCH', body)
export const deleteTask = (id: number) =>
  send<{ deleted: number }>(`/api/tasks/${id}`, 'DELETE')

// ---- notes (2026-09-13). STAFF only on every path; a TECH gets 403. ----

export type OpportunityNote = {
  id: number
  opportunity_id: number
  body: string
  created_by_id: number | null
  created_by: string | null
  created_at: string
  updated_at: string
}

/** A NOTE event on the contact's thread, shown read-only under the deal's notes. */
export type ContactThreadNote = {
  id: number
  conversation_id: number
  body: string
  occurred_at: string
}

export const listNotes = (oppId: number) =>
  get<{ notes: OpportunityNote[]; contact_notes: ContactThreadNote[] }>(
    `/api/opportunities/${oppId}/notes`)
export const createNote = (oppId: number, body: string) =>
  send<OpportunityNote>(`/api/opportunities/${oppId}/notes`, 'POST', { body })
export const patchNote = (id: number, body: string) =>
  send<OpportunityNote>(`/api/opportunity-notes/${id}`, 'PATCH', { body })
export const deleteNote = (id: number) =>
  send<{ deleted: number }>(`/api/opportunity-notes/${id}`, 'DELETE')

export type LinkedAppointment = {
  id: number
  title: string
  starts_at: string
  ends_at: string
  status: string
  calendar_name: string | null
  location: string | null
}

export const getOpportunity = (id: number) =>
  get<OpportunityDetail>(`/api/opportunities/${id}`)

export const patchOpportunity = (id: number, body: OpportunityPatch) =>
  send<OpportunityDetail>(`/api/opportunities/${id}/detail`, 'PATCH', body)

export const createOpportunity = (body: {
  title: string
  pipeline_id: number
  stage_id: number
  contact_id?: number | null
  value_cents: number
  /** Answers given in the Add opportunity dialog, validated by the same server
      code the detail form goes through. */
  custom_fields?: Record<string, unknown>
  // GoHighLevel's Add new opportunity modal files all of these in one submit
  // (2026-09-14). Every one optional: the API's machine callers send none.
  status?: string
  owner_id?: number | null
  follower_ids?: number[]
  business_name?: string | null
  source?: string | null
  address_street?: string | null
  address_city?: string | null
  address_state?: string | null
  address_postal_code?: string | null
  /** Zuper v2: a card created lost/abandoned needs one. */
  lead_outcome?: string | null
  lead_outcome_note?: string | null
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
  /** Real money per status. The Opportunity value card used to draw Lost as $0
   *  and Open as `total - won`, which is only true when nothing has been lost. */
  value_by_status: Record<string, number>
  /** Won / (Won + Lost) — of the deals that have been decided. */
  conversion_rate: number
  /** Won / every opportunity. The card's settings choose which one it shows. */
  conversion_rate_all: number
  range: { start: string | null; end: string | null }
}

export type FunnelStage = {
  id: number
  name: string
  position: number
  /** Sitting in this stage now — the same number the kanban column shows. */
  count: number
  value_cents: number
  /** Got this far: this stage's count plus every stage after it. */
  reached: number
  /** `reached / reached[0]`. Starts at 100% and only falls. `null` if nothing
   *  reached the pipeline at all, which has no denominator. */
  cumulative_pct: number | null
  /** `reached[i+1] / reached[i]`. `null` on the last stage — nothing follows it. */
  next_step_pct: number | null
}

export type DashboardFunnel = {
  pipeline_id: number | null
  pipeline_name: string | null
  total: number
  stages: FunnelStage[]
}

export type DashboardQuery = { pipelineId?: number; start?: string; end?: string }

/**
 * Dashboard query string.
 *
 * Built with URLSearchParams and never by concatenation: an ISO timestamp's
 * `+00:00` decodes as a space when it is pasted straight into a URL, and the date
 * filter then silently matches nothing. That has bitten this project before --
 * see "Things that have bitten us" in CLAUDE.md.
 */
function dashboardQuery(q: DashboardQuery): string {
  const sp = new URLSearchParams()
  if (q.pipelineId) sp.set('pipeline_id', String(q.pipelineId))
  if (q.start) sp.set('start', q.start)
  if (q.end) sp.set('end', q.end)
  const s = sp.toString()
  return s ? '?' + s : ''
}

export const getDashboard = (q: DashboardQuery = {}) =>
  get<DashboardStats>('/api/dashboard' + dashboardQuery(q))

export const getDashboardFunnel = (q: DashboardQuery = {}) =>
  get<DashboardFunnel>('/api/dashboard/funnel' + dashboardQuery(q))

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

/**
 * The ctrl+K palette. ONE request, not three: see the comment on /api/search in
 * backend/app/main.py. `limit` is the per-group cap, and the response says how
 * many matches each group really had.
 */
export const globalSearch = (q: string, limit = 5) =>
  get<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`)

export type ForecastStage = {
  stage_id: number
  name: string
  position: number
  count: number
  value_cents: number
  open_count: number
  open_value_cents: number
  won_count: number
  won_value_cents: number
  weighted_value_cents: number
  projected_value_cents: number
}

export type Forecast = {
  pipeline_id: number
  pipeline_name: string
  conversion_rate: number
  status: Record<string, number>
  stages: ForecastStage[]
  totals: {
    count: number
    value_cents: number
    open_count: number
    open_value_cents: number
    won_count: number
    won_value_cents: number
    weighted_value_cents: number
    projected_value_cents: number
  }
}

/**
 * Projected revenue by stage. STAFF-only, exactly like `/api/dashboard`, whose
 * conversion rate and totals it repeats rather than recomputing -- so the callers
 * gate on the role first instead of opening the tab onto a 403.
 */
export const getForecast = (pipelineId: number) =>
  get<Forecast>(`/api/forecast?pipeline_id=${pipelineId}`)

export type BulkStageMoved = {
  stage_id: number
  moved: number[]
  unchanged: number[]
  /** Per opportunity that actually changed stage: what rule 4 did about it. */
  automation: Record<string, string>
}

/**
 * Move a selection into one stage. ANY_USER, exactly like a single drag -- a TECH
 * may move a deal between stages, they just cannot edit it.
 *
 * There is deliberately no bulk delete to pair with these: see the comment above
 * the endpoints in backend/app/main.py.
 */
export const bulkMoveStage = (ids: number[], stageId: number) =>
  send<BulkStageMoved>('/api/opportunities/bulk/stage', 'POST', {
    ids,
    stage_id: stageId,
  })

export type BulkOwnerAssigned = { owner_id: number | null; updated: number[] }

/** Assign a selection an owner, or `null` to unassign. STAFF, like the detail PATCH. */
export const bulkAssignOwner = (ids: number[], ownerId: number | null) =>
  send<BulkOwnerAssigned>('/api/opportunities/bulk/owner', 'POST', {
    ids,
    owner_id: ownerId,
  })

/**
 * A named filter set for the Opportunities board -- GHL calls these smart lists.
 *
 * `pipeline_id` is nullable on purpose: a view that only says "Won, matching
 * 'skylight'" applies to whichever pipeline is open, while one that names a
 * pipeline switches the board to it.
 *
 * The board's built-in "Open opportunities" is NOT one of these. It is the
 * board's default state, so there is no row to delete and nothing to seed.
 */
export type SavedView = {
  id: number
  name: string
  pipeline_id: number | null
  pipeline_name: string | null
  status: string
  q: string
  position: number
  created_by_id: number | null
}

export const listSavedViews = () => get<SavedView[]>('/api/saved-views')

export const createSavedView = (body: {
  name: string
  pipeline_id?: number | null
  status?: string
  q?: string
}) => send<SavedView>('/api/saved-views', 'POST', body)

export const patchSavedView = (id: number, body: Partial<SavedView>) =>
  send<SavedView>(`/api/saved-views/${id}`, 'PATCH', body)

/** ADMIN, per this app's standing rule: everyone reads, staff write, admin deletes. */
export const deleteSavedView = (id: number) =>
  send<{ deleted: number }>(`/api/saved-views/${id}`, 'DELETE')

/**
 * Pipeline structure. ALL ADMIN.
 *
 * There were no write endpoints for pipelines or stages before 2026-09-10; the
 * structure came from the seed. Two rules do the safety work, and both live on the
 * server -- see the section comment in backend/app/main.py:
 *
 *   * only an EMPTY stage or pipeline can be deleted, with no `force` anywhere;
 *   * reordering writes stage positions only, and never an opportunity's stage.
 *
 * Names are deliberately not unique: the measured pipeline has two distinct
 * stages both called "Call Back", and the `ghl` CLI exits 5 rather than guess.
 */
export const createPipeline = (name: string) =>
  send<{ id: number; name: string; position: number; stages: [] }>(
    '/api/pipelines', 'POST', { name })

export const renamePipeline = (id: number, name: string) =>
  send<{ id: number; name: string }>(`/api/pipelines/${id}`, 'PATCH', { name })

export type PipelineDeleted = {
  deleted: number
  detached_saved_views: number[]
  detached_calendars: number[]
  /** Definitions that lose the ATTACHMENT, never the field and never an answer. */
  detached_custom_fields: number[]
}

export const deletePipeline = (id: number) =>
  send<PipelineDeleted>(`/api/pipelines/${id}`, 'DELETE')

export const createStage = (pipelineId: number, name: string) =>
  send<Stage & { pipeline_id: number }>(
    `/api/pipelines/${pipelineId}/stages`, 'POST', { name })

export const renameStage = (id: number, name: string) =>
  send<{ id: number; pipeline_id: number; name: string; position: number }>(
    `/api/stages/${id}`, 'PATCH', { name })

export const deleteStage = (id: number) =>
  send<{ deleted: number; pipeline_id: number }>(`/api/stages/${id}`, 'DELETE')

/** The full stage list in its new order -- a permutation, never a single move. */
export const reorderStages = (pipelineId: number, stageIds: number[]) =>
  send<{ pipeline_id: number; stages: { id: number; name: string; position: number }[] }>(
    `/api/pipelines/${pipelineId}/stages/reorder`, 'POST', { stage_ids: stageIds })

/**
 * Custom field definitions -- the job questions the owner defines himself.
 *
 * Reading is open to every role because the opportunity form has to render the
 * questions for whoever opens a deal. Every write is ADMIN, so the panel disables
 * what a role may not use rather than letting a form 403 on submit (d1f7c50,
 * b943f4b).
 *
 * DELETE ARCHIVES. It never removes the definition and never touches a recorded
 * answer; `restoreCustomField` brings the question back. There is no endpoint in
 * this app that destroys an answer, and there is deliberately no client for one.
 */
export type { FieldDef as CustomField } from './customFields'

export const listCustomFields = () =>
  get<import('./customFields').FieldDef[]>('/api/custom-fields')

export const createCustomField = (body: {
  label: string
  field_type: string
  options?: string[]
  pipeline_ids: number[]
  group_id?: number | null
  script?: string | null
  linked_field?: import('./customFields').LinkedField | null
  details_when?: string[]
}) => send<import('./customFields').FieldDef>('/api/custom-fields', 'POST', body)

export const patchCustomField = (id: number, body: {
  label?: string
  options?: string[]
  pipeline_ids?: number[]
  /** null moves the field back under Opportunity details. */
  group_id?: number | null
  /** Settings, not answers: clearing one ('' / null / []) touches no answer. */
  script?: string | null
  linked_field?: import('./customFields').LinkedField | null
  details_when?: string[]
}) => send<import('./customFields').FieldDef>(`/api/custom-fields/${id}`, 'PATCH', body)

/** Archives. The name says what it does to the DEFINITION; the answers stay. */
export const archiveCustomField = (id: number) =>
  send<{ archived: number; key: string; note: string }>(
    `/api/custom-fields/${id}`, 'DELETE')

export const restoreCustomField = (id: number) =>
  send<import('./customFields').FieldDef>(
    `/api/custom-fields/${id}/restore`, 'POST')

/** The whole live list as a permutation, never a single "move" instruction. */
export const reorderCustomFields = (fieldIds: number[]) =>
  send<import('./customFields').FieldDef[]>(
    '/api/custom-fields/reorder', 'POST', { field_ids: fieldIds })

// ---------------------------------------------------------------------------
// The Pipelines tab, its Create/Edit modal and its row menu (2026-09-13).
//
// Appended rather than folded into the helpers above so a branch working on the
// opportunity card and modal merges without touching the same lines. The shapes
// live in lib/pipelines.ts, which is import-free so node can run it in tests.
//
// Roles, enforced by the server and mirrored by the screen: DISPATCHER may create,
// edit, duplicate and reorder; ADMIN alone deletes and manages permissions.
// ---------------------------------------------------------------------------

export type { ColorMode, PipelineRow, StageRow } from './pipelines'

type PipelineRowT = import('./pipelines').PipelineRow

/** GET /api/pipelines, with every setting the modal edits. Same request as `listPipelines`. */
export const listPipelineRows = () => get<PipelineRowT[]>('/api/pipelines')

export const createPipelineWithSettings = (body: ReturnType<typeof import('./pipelines').createBody>) =>
  send<PipelineRowT>('/api/pipelines', 'POST', body)

export type PipelineUpdated = PipelineRowT & {
  /** Stage id -> the deals moved out of it before it was deleted. */
  moved: Record<string, number[]>
}

export const updatePipeline = (id: number, body: ReturnType<typeof import('./pipelines').updateBody>) =>
  send<PipelineUpdated>(`/api/pipelines/${id}`, 'PATCH', body)

/** "<name> (copy)" with every setting and stage. Never a deal. */
export const duplicatePipeline = (id: number) =>
  send<PipelineRowT>(`/api/pipelines/${id}/duplicate`, 'POST')

/** Every pipeline the caller can see, in the new order. */
export const reorderPipelines = (ids: number[]) =>
  send<PipelineRowT[]>('/api/pipelines/reorder', 'POST', { pipeline_ids: ids })

export type PipelineAccess = { pipeline_id: number; user_ids: number[]; everyone: boolean }

export const getPipelinePermissions = (id: number) =>
  get<PipelineAccess>(`/api/pipelines/${id}/permissions`)

/** Nobody selected = everyone can access it. ADMIN always can. */
export const setPipelinePermissions = (id: number, userIds: number[]) =>
  send<PipelineAccess>(`/api/pipelines/${id}/permissions`, 'PUT', { user_ids: userIds })

export type PipelineDeletedMoving = PipelineDeleted & { moved_opportunities: number[] }

/**
 * Delete a pipeline. With deals in it, `moveToStageId` (a stage of ANOTHER
 * pipeline) is where every one of them goes first — nothing is ever deleted but
 * the pipeline and its columns. Without deals, pass nothing.
 */
export const deletePipelineMovingDeals = (id: number, moveToStageId?: number) => {
  const sp = new URLSearchParams()
  if (moveToStageId != null) sp.set('move_to_stage_id', String(moveToStageId))
  const qs = sp.toString()
  return send<PipelineDeletedMoving>(`/api/pipelines/${id}${qs ? '?' + qs : ''}`, 'DELETE')
}

/** The Stage distribution card's slices: only stages with "Show in reports" pie on. */
export type DashboardDistribution = DashboardFunnel & {
  distribution: { id: number; name: string; position: number; color: string | null;
    count: number; value_cents: number }[]
  distribution_total: number
}

/** Which probability weighted a forecast row — see GET /api/forecast. */
export type ForecastWeighting = 'opportunity' | 'stage' | 'conversion_rate'

export type ForecastWithProbability = Omit<Forecast, 'stages'> & {
  use_opportunity_probability: boolean
  stages: (ForecastStage & { probability: number | null; weighting: ForecastWeighting })[]
}

/**
 * Custom field GROUPS — the modal's custom tabs ("Roof Inspection", "Photo
 * Checklist"), 2026-09-13. A group applies to every pipeline and holds no answer:
 * deleting one moves its fields back to Opportunity details. Writes are ADMIN.
 */
export type FieldGroup = {
  id: number
  name: string
  position: number
  field_ids: number[]
}

export const listFieldGroups = () => get<FieldGroup[]>('/api/custom-field-groups')
export const createFieldGroup = (name: string) =>
  send<FieldGroup>('/api/custom-field-groups', 'POST', { name })
export const renameFieldGroup = (id: number, name: string) =>
  send<FieldGroup>(`/api/custom-field-groups/${id}`, 'PATCH', { name })
export const reorderFieldGroups = (groupIds: number[]) =>
  send<FieldGroup[]>('/api/custom-field-groups/reorder', 'POST', { group_ids: groupIds })
export const deleteFieldGroup = (id: number) =>
  send<{ deleted: number; moved_field_ids: number[] }>(
    `/api/custom-field-groups/${id}`, 'DELETE')

// ---------- blocked off time (the Book appointment modal's second tab) ----------

/** A range blocked off on a calendar. Sends nothing and enqueues nothing. */
export type BlockedTime = {
  id: number
  title: string
  calendar_id: number
  calendar_name: string | null
  color: string
  starts_at: string
  ends_at: string
  notes: string | null
}

export function listBlockedTimes(p: {
  start: string
  end: string
  calendar_ids?: number[]
  user_ids?: number[]
}) {
  const sp = new URLSearchParams({ start: p.start, end: p.end })
  if (p.calendar_ids?.length) sp.set('calendar_ids', p.calendar_ids.join(','))
  if (p.user_ids?.length) sp.set('user_ids', p.user_ids.join(','))
  return get<BlockedTime[]>(`/api/blocked-times?${sp}`)
}

export type BlockedTimeBody = {
  title: string
  calendar_id: number
  starts_at: string
  ends_at: string
  notes?: string | null
}

export const getBlockedTime = (id: number) =>
  get<BlockedTime>(`/api/blocked-times/${id}`)
export const createBlockedTime = (body: BlockedTimeBody) =>
  send<BlockedTime>('/api/blocked-times', 'POST', body)
export const patchBlockedTime = (id: number, body: Partial<BlockedTimeBody>) =>
  send<BlockedTime>(`/api/blocked-times/${id}`, 'PATCH', body)
export const deleteBlockedTime = (id: number) =>
  send<{ deleted: number }>(`/api/blocked-times/${id}`, 'DELETE')

/**
 * ---------------------------------------------------------------------------
 * One inbox, two kinds of thread (2026-09-13).
 *
 * A row from `GET /api/conversations` is either a contact's thread or a thread
 * for a phone number nobody has saved. They share the list, the badge and the
 * composer; they live at different URLs. These helpers take the ROW, so the page
 * never has to branch on the kind itself — and never addresses a number thread's
 * id at a contact thread's route, where the same number means someone else.
 * ---------------------------------------------------------------------------
 */
type ThreadRef = Pick<ConversationSummary, 'id' | 'kind'>

const threadPath = (t: ThreadRef) =>
  t.kind === 'number' ? `/api/number-threads/${t.id}` : `/api/conversations/${t.id}`

export const listThreadEvents = (t: ThreadRef, filter: string) =>
  get<ThreadEvent[]>(`${threadPath(t)}/events?filter=${encodeURIComponent(filter)}`)

export const patchThread = (t: ThreadRef, body: { read?: boolean; starred?: boolean }) =>
  send<ConversationSummary>(threadPath(t), 'PATCH', body)

export const deleteThread = (t: ThreadRef) =>
  send<{ deleted: number; events_deleted: number }>(threadPath(t), 'DELETE')

export const sendToThread = (t: ThreadRef, body: string, type: SendableType,
                            attachmentIds: number[] = []) =>
  send<SentMessage>(`${threadPath(t)}/messages`, 'POST',
                    { body, type, attachment_ids: attachmentIds })

/** Ring the thread's number — a contact's, or a number nobody has saved — through the
 *  same owen-main click-to-call path. */
export const callThread = (t: ThreadRef) =>
  send<CallPlaced>(`${threadPath(t)}/call`, 'POST')

/** What `POST /api/contacts` reports when saving the contact adopted a number-only
 *  thread: its whole history now sits on `conversation_id`. */
export type AdoptedThread = {
  number_thread_id: number
  events_moved: number
  duplicates_skipped: number
  conversation_id: number | null
} | null

/** The top-bar status dot's link and Quo checks (2026-09-14). Asked of owen-main by the
 *  backend, cached 30s there; always a 200 — an unreachable phone system is a state in the
 *  body, not an error. Shape: `ServerStatus` in lib/connectionStatus.ts. */
export const fetchConnectionStatus = () =>
  get<import('./connectionStatus').ServerStatus>('/api/connection-status')

/* ---------------------------------------------------------------------------
 * CompanyCam job photos (2026-09-14). Read on demand by the backend; the browser
 * never sees the token or a CompanyCam image URL — every image is a CRM path.
 * Shapes and the shared logic: lib/companycam.ts.
 * ---------------------------------------------------------------------------
 */
export const getOpportunityCompanyCam = (opportunityId: number, refresh = false) =>
  get<import('./companycam').CompanyCamProjects>(
    `/api/opportunities/${opportunityId}/companycam${refresh ? '?refresh=true' : ''}`)

export const getCompanyCamPhotos = (opportunityId: number, projectId: string, page: number,
                                    refresh = false) =>
  get<import('./companycam').CompanyCamPhotoPage>(
    `/api/opportunities/${opportunityId}/companycam/projects/${encodeURIComponent(projectId)}`
    + `/photos?page=${page}${refresh ? '&refresh=true' : ''}`)

export const getContactCompanyCam = (contactId: number) =>
  get<import('./companycam').ContactCompanyCamProjects>(`/api/contacts/${contactId}/companycam`)

export type CompanyCamStatus = {
  token_set: boolean
  create_projects: boolean
  sync_enabled: boolean
  heartbeat: {
    last_started_at: string | null
    last_finished_at: string | null
    last_success_at: string | null
    last_full_sweep_at: string | null
    last_counts: Record<string, number | boolean> | null
    last_error: string | null
  }
  links_by_method: Record<string, number>
  project_requests: Record<string, number>
  review_open: number
}

export type CompanyCamReviewItem = {
  id: number
  project_id: string
  project_name: string | null
  project_address: string | null
  first_seen_at: string
  candidates: { id: number; title: string; contact_name: string | null;
    pipeline_name: string | null; address: string | null }[]
}

export const getCompanyCamStatus = () => get<CompanyCamStatus>('/api/companycam/status')

export const listCompanyCamReview = () => get<CompanyCamReviewItem[]>('/api/companycam/review')

export const linkCompanyCamReview = (itemId: number, opportunityIds: number[]) =>
  send<{ project_id: string; linked: number[] }>(
    `/api/companycam/review/${itemId}/link`, 'POST', { opportunity_ids: opportunityIds })

export const dismissCompanyCamReview = (itemId: number) =>
  send<{ dismissed: number }>(`/api/companycam/review/${itemId}/dismiss`, 'POST')

export const unlinkCompanyCamProject = (opportunityId: number, projectId: string) =>
  send<{ unlinked: string }>(
    `/api/opportunities/${opportunityId}/companycam/projects/${encodeURIComponent(projectId)}`,
    'DELETE')

/* ---------------------------------------------------------------------------
 * Placing a call that rings THIS browser first (2026-09-14).
 *
 * `ringBrowser` asks owen-main to ring the signed-in user's own browser phone rather
 * than the binding's default operator; the server names the operator from the session,
 * never from the request. Do not call these directly from a screen: go through
 * `useCallLauncher` (lib/callLauncher.ts), which marks the outbound intent BEFORE the
 * request leaves so the browser answers its own leg instead of showing "Incoming call".
 * ---------------------------------------------------------------------------
 */
export type DialPlaced = CallPlaced & {
  /** The number as the server normalised it (`+1XXXXXXXXXX`), null when it refused it. */
  number: string | null
  contact_id?: number | null
  contact_name?: string | null
  conversation_id?: number | null
  number_thread_id?: number | null
}

/** The Conversations dialer: any number. A contact's number is their call, anyone
 *  else's is logged on its number-only thread. Never creates a contact. */
export const dialNumber = (number: string, ringBrowser: boolean) =>
  send<DialPlaced>('/api/calls/dial', 'POST', { number, ring_browser: ringBrowser })

export const callThreadRinging = (t: ThreadRef, ringBrowser: boolean) =>
  send<CallPlaced>(`${threadPath(t)}/call`, 'POST', { ring_browser: ringBrowser })

export const callContactRinging = (contactId: number, ringBrowser: boolean) =>
  send<CallPlaced>(`/api/contacts/${contactId}/call`, 'POST', { ring_browser: ringBrowser })

// ---------------- My Staff (2026-09-15) ----------------

/** A user as My Staff reads it — what `GET /api/users` answers an ADMIN. */
export type StaffUser = {
  id: number
  name: string
  email: string
  role: 'ADMIN' | 'DISPATCHER' | 'TECH'
  is_active: boolean
  only_assigned_data: boolean
  phone: string | null
  phone_display: string | null
  must_change_password: boolean
  /** No password: a token-only account (the telephony feed). Never made a login. */
  machine: boolean
}

export type StaffCalendar = {
  id: number; name: string; created: boolean; renamed_from?: string | null
}

export const listStaff = (params: { q?: string; role?: string } = {}) => {
  const sp = new URLSearchParams()
  if (params.q?.trim()) sp.set('q', params.q.trim())
  if (params.role) sp.set('role', params.role)
  const query = sp.toString()
  return get<StaffUser[]>('/api/users' + (query ? '?' + query : ''))
}

export type StaffCreate = {
  name: string; email: string; phone: string | null; role: string
  password: string; only_assigned_data: boolean
}

export const createStaffUser = (body: StaffCreate) =>
  send<StaffUser & { calendar: StaffCalendar | null }>('/api/users', 'POST', body)

export type StaffPatch = Partial<{
  name: string; email: string; phone: string | null; role: string
  is_active: boolean; only_assigned_data: boolean; password: string
}>

export const patchStaffUser = (id: number, body: StaffPatch) =>
  send<StaffUser>(`/api/users/${id}`, 'PATCH', body)

// ---------------- AI Agents (2026-09-15) ----------------
//
// Everything under /api/ai. ADMIN and DISPATCHER only (not a restricted user); the
// server refuses the rest. No response here ever carries a provider API key: a
// connection reads "•••• last4".

import type { Draft as AiDraft } from './aiAgents'

export type AiCatalogueAction = {
  name: string; label: string; description: string; kind: string; channels: string[]; phase: number
}
export type AiCatalogue = {
  actions: AiCatalogueAction[]
  triggers: { type: string; label: string; help: string; params: Record<string, string> }[]
  channels: { value: string; label: string; available: boolean; phase?: number }[]
  providers: { value: string; label: string; default_model: string; needs_base_url: boolean }[]
  known_prices: Record<string, { input: string | null; output: string | null }>
  modes: string[]
  days: string[]
  timezone: string
  secrets_configured: boolean
  default_config: AiDraft
}
export const aiCatalogue = () => get<AiCatalogue>('/api/ai/catalogue')

export type AiSettings = {
  paused: boolean; on_call_phone: string | null; on_call_phone_display: string | null
  updated_at: string | null; secrets_configured: boolean
}
export const aiSettings = () => get<AiSettings>('/api/ai/settings')
export const saveAiSettings = (body: { paused?: boolean; on_call_phone?: string | null }) =>
  send<AiSettings>('/api/ai/settings', 'PUT', body)

export type AiConnection = {
  id: number; name: string; provider: string; base_url: string | null; api_key: string
  default_model: string; price_input: string | null; price_output: string | null
  created_at: string | null; updated_at: string | null; agents: number
}
export const aiConnections = () => get<AiConnection[]>('/api/ai/connections')
export const aiConnectionNames = () =>
  get<{ id: number; name: string; provider: string; default_model: string }[]>('/api/ai/connection-names')
export const createAiConnection = (body: Record<string, unknown>) =>
  send<AiConnection>('/api/ai/connections', 'POST', body)
export const updateAiConnection = (id: number, body: Record<string, unknown>) =>
  send<AiConnection>(`/api/ai/connections/${id}`, 'PATCH', body)
export const deleteAiConnection = (id: number) =>
  send<{ deleted: number }>(`/api/ai/connections/${id}`, 'DELETE')
export type AiProbe = {
  provider: string; base_url?: string | null; api_key?: string | null; model?: string | null
  connection_id?: number | null
}
export const testAiConnection = (body: AiProbe) =>
  send<{ ok: boolean; sentence: string; latency_ms: number }>('/api/ai/connections/test', 'POST', body)
export const loadAiModels = (body: AiProbe) =>
  send<{ ok: boolean; sentence: string; models: string[] }>('/api/ai/connections/models', 'POST', body)

export type AiFolder = { id: number; name: string; agents: number }
export const aiFolders = () => get<AiFolder[]>('/api/ai/folders')
export const createAiFolder = (name: string) => send<AiFolder>('/api/ai/folders', 'POST', { name })
export const renameAiFolder = (id: number, name: string) =>
  send<AiFolder>(`/api/ai/folders/${id}`, 'PATCH', { name })
export const deleteAiFolder = (id: number) =>
  send<{ deleted: number; agents_moved_out: number }>(`/api/ai/folders/${id}`, 'DELETE')

export type AiAgentRow = {
  id: number; name: string; description: string | null; channel: string; mode: string
  folder_id: number | null; folder_name: string | null; published_version: number | null
  published_at: string | null; has_unpublished_changes: boolean; last_run_at: string | null
  last_outcome: string | null; updated_at: string | null; triggers: string[]
  /** The PUBLISHED version's triggers — what "Run AI agent" can actually use. */
  published_triggers?: string[]
}
export type AiAgentDetail = AiAgentRow & {
  draft: AiDraft
  compiled_prompt: string
  publish_problems: string[]
  versions: { id: number; version: number; published_at: string | null; published_by: string | null; current: boolean }[]
  can_edit: boolean
}
export const aiAgents = () => get<AiAgentRow[]>('/api/ai/agents')
export const aiAgent = (id: number) => get<AiAgentDetail>(`/api/ai/agents/${id}`)
export const createAiAgent = (body: { name: string; description?: string | null; folder_id?: number | null; template_id?: number | null }) =>
  send<AiAgentDetail>('/api/ai/agents', 'POST', { ...body, channel: 'text' })
export const updateAiAgent = (id: number, body: { name?: string; description?: string | null; folder_id?: number | null; draft?: AiDraft }) =>
  send<AiAgentDetail>(`/api/ai/agents/${id}`, 'PATCH', body)
export const previewAiPrompt = (id: number, draft: AiDraft, name?: string) =>
  send<{ compiled_prompt: string; publish_problems: string[] }>(`/api/ai/agents/${id}/compiled-prompt`, 'POST', { draft, name })
export const publishAiAgent = (id: number) => send<AiAgentDetail>(`/api/ai/agents/${id}/publish`, 'POST')
export const setAiAgentMode = (id: number, mode: string, confirm = false) =>
  send<AiAgentDetail>(`/api/ai/agents/${id}/mode`, 'POST', { mode, confirm })
export const deleteAiAgent = (id: number) => send<{ deleted: number }>(`/api/ai/agents/${id}`, 'DELETE')
export const duplicateAiAgent = (id: number) => send<AiAgentDetail>(`/api/ai/agents/${id}/duplicate`, 'POST')

export type AiStep = {
  id: number; position: number; kind: string; text: string | null; tool_name: string | null
  tool_call_id: string | null; data: Record<string, unknown> | null; action_status: string | null
  created_at: string | null
}
export type AiWould = { action: string; label: string; summary: string; arguments: Record<string, unknown> }
export type AiTokens = { input: number; output: number; cache_write: number; cache_read: number }
export type AiTryResult = {
  run_id: number; outcome: string; reason: string | null; reply: string; would: AiWould[]
  tokens: AiTokens; cost: string | null; latency_ms: number | null; steps: AiStep[]
}
export const tryAiAgent = (id: number, body: {
  messages: { role: 'user' | 'assistant'; content: string }[]
  contact_id?: number | null; opportunity_id?: number | null; draft?: AiDraft
}) => send<AiTryResult>(`/api/ai/agents/${id}/try`, 'POST', body)
export const runAiAgent = (id: number, body: { contact_id?: number | null; opportunity_id?: number | null }) =>
  send<{ run_id: number; outcome: string; reason: string | null; mode: string }>(`/api/ai/agents/${id}/run`, 'POST', body)

export type AiTemplate = {
  id: number; name: string; description: string | null; channel: string; created_at: string | null
  actions: string[]; triggers: string[]
}
export const aiTemplates = () => get<AiTemplate[]>('/api/ai/templates')
export const saveAiTemplate = (body: { agent_id: number; name: string; description?: string | null }) =>
  send<{ id: number; name: string }>('/api/ai/templates', 'POST', body)
export const deleteAiTemplate = (id: number) => send<{ deleted: number }>(`/api/ai/templates/${id}`, 'DELETE')

export type AiKb = {
  id: number; name: string; description: string | null; faqs: number; articles: number; files: number
  agents: string[]; updated_at: string | null
}
export type AiKbItem = {
  id: number; kb_id: number; kind: 'faq' | 'article' | 'file'; title: string; body: string
  content_type: string | null; size_bytes: number | null; created_at: string | null
  updated_at: string | null; characters: number
}
export const aiKbs = () => get<AiKb[]>('/api/ai/knowledge-bases')
export const createAiKb = (body: { name: string; description?: string | null }) =>
  send<AiKb>('/api/ai/knowledge-bases', 'POST', body)
export const updateAiKb = (id: number, body: { name: string; description?: string | null }) =>
  send<AiKb>(`/api/ai/knowledge-bases/${id}`, 'PATCH', body)
export const deleteAiKb = (id: number) => send<{ deleted: number }>(`/api/ai/knowledge-bases/${id}`, 'DELETE')
export const aiKbItems = (kbId: number) => get<AiKbItem[]>(`/api/ai/knowledge-bases/${kbId}/items`)
export const addAiKbItem = (kbId: number, body: { kind: 'faq' | 'article'; title: string; body: string }) =>
  send<AiKbItem>(`/api/ai/knowledge-bases/${kbId}/items`, 'POST', body)
export const uploadAiKbFile = (kbId: number, body: { filename: string; content_type: string | null; data: string }) =>
  send<AiKbItem>(`/api/ai/knowledge-bases/${kbId}/files`, 'POST', body)
export const updateAiKbItem = (id: number, body: { title?: string; body?: string }) =>
  send<AiKbItem>(`/api/ai/kb-items/${id}`, 'PATCH', body)
export const deleteAiKbItem = (id: number) => send<{ deleted: number }>(`/api/ai/kb-items/${id}`, 'DELETE')
export const searchAiKbs = (query: string, knowledge_base_ids: number[]) =>
  send<{ useful: boolean; results: { item_id: number; kb_id: number; title: string; kind: string; text: string; score: number }[] }>(
    '/api/ai/knowledge-bases/search', 'POST', { query, knowledge_base_ids })

export type AiGap = {
  id: number; question: string; status: string; count: number; first_seen_at: string | null
  last_seen_at: string | null; last_run_id: number | null; agent_id: number | null
  agent_name: string | null; resolved_item_id: number | null
}
export const aiGaps = (status = 'open') => get<AiGap[]>(`/api/ai/knowledge-gaps?status=${encodeURIComponent(status)}`)
export const resolveAiGap = (id: number, body: { knowledge_base_id: number; answer: string; question?: string }) =>
  send<{ id: number; status: string }>(`/api/ai/knowledge-gaps/${id}/resolve`, 'POST', body)
export const dismissAiGap = (id: number) =>
  send<{ id: number; status: string }>(`/api/ai/knowledge-gaps/${id}/dismiss`, 'POST')

export type AiRunRow = {
  id: number; agent_id: number; agent_name: string; version: number | null; trigger: string
  trigger_label: string; contact_id: number | null; opportunity_id: number | null
  appointment_id: number | null; subject: string | null; mode: string; is_test: boolean
  outcome: string; reason: string | null; provider: string | null; model: string | null
  tokens: AiTokens; cost: string | null; latency_ms: number | null; created_at: string | null
  run_after: string | null; started_at: string | null; finished_at: string | null
}
export type AiSuggestion = {
  id: number; run_id: number; agent_id: number; agent_name: string | null; action: string
  label: string; summary: string; args: Record<string, unknown>; contact_id: number | null
  opportunity_id: number | null; status: string; result: Record<string, unknown> | null
  created_at: string | null; decided_at: string | null; contact_name?: string | null
}
export type AiRunDetail = AiRunRow & { steps: AiStep[]; actions: AiStep[]; suggestions: AiSuggestion[] }
export const aiRuns = (params: {
  agent_id?: number | null; outcome?: string; since?: string; until?: string
  include_tests?: boolean; page?: number; page_size?: number
}) => {
  const sp = new URLSearchParams()
  if (params.agent_id != null) sp.set('agent_id', String(params.agent_id))
  if (params.outcome) sp.set('outcome', params.outcome)
  if (params.since) sp.set('since', params.since)
  if (params.until) sp.set('until', params.until)
  if (params.include_tests === false) sp.set('include_tests', 'false')
  sp.set('page', String(params.page ?? 1))
  sp.set('page_size', String(params.page_size ?? 50))
  return get<Page<AiRunRow>>(`/api/ai/runs?${sp}`)
}
export const aiRun = (id: number) => get<AiRunDetail>(`/api/ai/runs/${id}`)

export type AiMetrics = {
  days: number; runs: number
  per_day: { day: string; runs: number; cost: string | null; cost_unknown_runs: number }[]
  outcomes: Record<string, number>
  per_agent: { agent_id: number; agent_name: string; runs: number; cost: string | null; cost_unknown_runs: number }[]
  avg_latency_ms: number | null
  top_actions: { action: string; executed: number; suggested: number; refused: number }[]
  open_knowledge_gaps: number
  total_cost: string | null
}
export const aiMetrics = (days = 30) => get<AiMetrics>(`/api/ai/metrics?days=${days}`)

export const aiSuggestions = (params: { contact_id?: number; opportunity_id?: number; status?: string }) => {
  const sp = new URLSearchParams({ status: params.status ?? 'pending' })
  if (params.contact_id != null) sp.set('contact_id', String(params.contact_id))
  if (params.opportunity_id != null) sp.set('opportunity_id', String(params.opportunity_id))
  return get<AiSuggestion[]>(`/api/ai/suggestions?${sp}`)
}
export const approveAiSuggestion = (id: number) =>
  send<AiSuggestion>(`/api/ai/suggestions/${id}/approve`, 'POST')
export const dismissAiSuggestion = (id: number) =>
  send<AiSuggestion>(`/api/ai/suggestions/${id}/dismiss`, 'POST')

export type AiAlert = {
  id: number; kind: string; title: string; body: string | null; urgent: boolean
  run_id: number | null; agent_id: number | null; contact_id: number | null
  opportunity_id: number | null; task_id: number | null; created_at: string | null; read: boolean
}
export const aiAlerts = () => get<{ unread: number; items: AiAlert[] }>('/api/ai/alerts')
export const readAiAlert = (id: number) => send<{ id: number; read: boolean }>(`/api/ai/alerts/${id}/read`, 'POST')
export const readAllAiAlerts = () => send<{ marked: number }>('/api/ai/alerts/read-all', 'POST')

// ---------------- A live AI-agent call (2026-09-23) ----------------

/** One AI-agent call in progress, as `GET /api/live-calls` answers it (app/live_calls.py).
 *  `contact` is the CRM contact holding the caller's number, when there is one. */
export type LiveCall = {
  linkedid: string
  caller_number: string | null
  dialed_number: string | null
  agent: string | null
  started_at: string | null
  duration_s: number | null
  turns: number | null
  contact: { id: number; name: string | null } | null
}
/** Never an error for an unconfigured or unreachable phone system: `calls` is simply empty. */
export const listLiveCalls = () => get<{ calls: LiveCall[] }>('/api/live-calls')
/** Both ring the SIGNED-IN user's own browser line — the server sends their email, never ours. */
export const listenToLiveCall = (linkedid: string) =>
  send<{ ok: boolean; operator: string | null }>(`/api/live-calls/${encodeURIComponent(linkedid)}/listen`, 'POST')
export const takeOverLiveCall = (linkedid: string) =>
  send<{ ok: boolean; operator: string | null }>(`/api/live-calls/${encodeURIComponent(linkedid)}/takeover`, 'POST')

// ---------------- New message to any number (2026-09-15) ----------------

/** What `POST /api/messages/new` answers. `recorded: false` means nothing was written — a
 *  bad number or "Only assigned data" — and `reason` says why in a sentence. Otherwise the
 *  text is on the thread named by `key` ("c<id>" / "n<id>"), whatever the phone system
 *  then did with it (`reason` is the composer's vocabulary, see lib/sendOutcome.ts). */
export type NewMessageResult = {
  recorded: boolean
  suppressed?: boolean
  reason: string
  number: string | null
  kind: 'contact' | 'number' | null
  key: string | null
  id: number | null
  contact_id: number | null
  contact_name?: string | null
  conversation_id: number | null
  number_thread_id: number | null
  delivery_status: string | null
  delivery_detail: string | null
}

/** Text ANY number: a contact's number goes on their conversation, anyone else's on the
 *  number's own thread. Never creates a contact, and never takes a "from" number. */
export const sendNewMessage = (number: string, body: string,
                              attachmentIds: number[] = []) =>
  send<NewMessageResult>('/api/messages/new', 'POST',
                         { number, body, attachment_ids: attachmentIds })

/** One hard-coded automation rule and whether it is on (Settings → Automations). */
export type AutomationRule = {
  key: string
  name: string
  trigger: string
  texts_customer: boolean
  enabled: boolean
  reason: string | null
}
export const listAutomations = () => get<{ rules: AutomationRule[] }>('/api/automations')

// ---------------------------------------------------------------------------
// Pictures on a text message (2026-09-16).
//
// The bytes never travel as JSON. A picture is uploaded as a multipart form the moment the
// operator picks it, so the composer can show it before anything is sent and a 4 MB photo
// is not re-sent every time they edit the sentence; sending then passes ids.
//
// `uploadAttachment` does NOT go through `send()`, and deliberately: that helper sets
// `Content-Type: application/json`, and a multipart body needs the boundary the browser
// generates for it — setting the header by hand is the classic way to make a form upload
// arrive as an empty dict. Everything else about it is the same: same-origin, cookies,
// the CSRF token, and one retry after a refreshed access token.
// ---------------------------------------------------------------------------

async function upload<T>(path: string, file: File, retry = true): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch(BASE + path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRF-Token': cookie('ghl_csrf') ?? '' },
    body: form,
  })
  if (r.status === 401) {
    if (retry && (await refresh())) return upload<T>(path, file, false)
    window.dispatchEvent(new Event('ghl:unauthorized'))
    throw new Unauthorized('not signed in')
  }
  if (!r.ok) throw await failed(r)
  return r.json() as Promise<T>
}

/** Keep a picture so it can be previewed and then sent. Refused with a sentence if it is
 *  not an image or is too big — the server sniffs the bytes, it does not trust the type. */
export const uploadAttachment = (file: File) =>
  upload<Attachment>('/api/attachments', file)

/** Take an unsent picture back off the composer. Only a draft; a picture already on a
 *  message is part of the record of what was said. */
export const deleteAttachment = (id: number) =>
  send<{ deleted: number }>(`/api/attachments/${id}`, 'DELETE')

/** Fetch an inbound picture from the phone system again, now. Answers the attachment's new
 *  state whatever happened — including "it failed again", with the sentence saying why. */
export const retryAttachment = (id: number) =>
  send<Attachment>(`/api/attachments/${id}/retry`, 'POST')

// ---------------- Zuper two-way sync (2026-09-16) ----------------
// Routes: backend/app/zuper/api.py. Shapes and the words for them: lib/zuper.ts.

/** Settings → Zuper, everything on one read. ADMIN. */
export const getZuperStatus = () => get<import('./zuper').ZuperStatus>('/api/zuper/status')

/** The switch and the Workiz cutover date. 409 (a sentence) while a blocker remains. */
export const putZuperSettings = (body: { enabled?: boolean; workiz_cutover_date?: string | null }) =>
  send<import('./zuper').ZuperStatus>('/api/zuper/settings', 'PUT', body)

/** Read-only GETs against Zuper; the results gate the switch. */
export const runZuperSetupCheck = () =>
  send<import('./zuper').ZuperSetup>('/api/zuper/setup/check', 'POST')

/** "Confirmed by hand" for an item the Zuper API cannot report. Un-ticking pauses the sync. */
export const confirmZuperSetupItem = (key: string, confirmed: boolean) =>
  send<import('./zuper').ZuperSetup>(
    `/api/zuper/setup/confirmations/${encodeURIComponent(key)}`, 'PUT', { confirmed })

export const listZuperConflicts = (page: number, pageSize: number) =>
  get<import('./zuper').ZuperPage<import('./zuper').ZuperConflict>>(
    `/api/zuper/conflicts?${new URLSearchParams({ page: String(page), page_size: String(pageSize) })}`)

export const listZuperDeletes = (page: number, pageSize: number) =>
  get<import('./zuper').ZuperPage<import('./zuper').ZuperDelete>>(
    `/api/zuper/deletes?${new URLSearchParams({ page: String(page), page_size: String(pageSize) })}`)

/** Restore a delete the sync mirrored — its whole batch, parents first. */
export const restoreZuperDelete = (id: number) =>
  send<import('./zuper').ZuperRestoreResult>(`/api/zuper/deletes/${id}/restore`, 'POST')

/** A card's quotes and invoices as Zuper last reported them. 404 = a card the reader cannot see. */
export const getOpportunityZuper = (opportunityId: number) =>
  get<import('./zuper').ZuperOpportunityMoney>(`/api/opportunities/${opportunityId}/zuper`)

/** The documents on this customer's cards that the reader can see. */
export const getContactZuper = (contactId: number) =>
  get<import('./zuper').ZuperContactMoney>(`/api/contacts/${contactId}/zuper`)

/** Zuper's job attachments. Every `url` is a CRM relay path, never Zuper's. */
export const getOpportunityZuperAttachments = (opportunityId: number) =>
  get<import('./zuper').ZuperAttachments>(`/api/opportunities/${opportunityId}/zuper/attachments`)

// ---------------- Zuper v2 (2026-09-16): Send to Zuper, lead outcomes ----------------

/** 202 {state: "queued"}; 200 {state, already: true} for a second press; 409 with sentences. */
export const sendToZuper = (opportunityId: number) =>
  send<{ state: import('./zuper').ZuperSendState; already?: boolean }>(
    `/api/opportunities/${opportunityId}/zuper/send`, 'POST')

/** Set one lead outcome on a selection. STAFF. */
export const bulkLeadOutcome = (ids: number[], leadOutcome: string, note: string | null) =>
  send<{ updated: number[] }>('/api/opportunities/bulk/lead-outcome', 'POST',
    { ids, lead_outcome: leadOutcome, lead_outcome_note: note })

/** Outcomes by source and by campaign over a period (STAFF, like the other reports). */
export const getLeadOutcomeReport = (p: { since: string; until: string; pipeline_id?: number | null }) => {
  const sp = new URLSearchParams({ since: p.since, until: p.until })
  if (p.pipeline_id) sp.set('pipeline_id', String(p.pipeline_id))
  return get<import('./zuper').LeadOutcomeReport>(`/api/reports/lead-outcomes?${sp}`)
}
