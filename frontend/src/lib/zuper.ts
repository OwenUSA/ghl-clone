/**
 * Zuper two-way sync (2026-09-16) — the words and numbers Settings → Zuper, the opportunity
 * modal's "Quotes & invoices", the contact panel and the Photos tab share.
 *
 * Import-free, so backend/tests/test_zuper_ui.py executes it under node: the rule a
 * conflict was decided by, a field's name, money from cents, a date in the account's zone,
 * which panels are drawn at all, and the deep link the sync writes into Zuper's "CRM Link"
 * field are behaviour, not markup.
 *
 * The routes are backend/app/zuper/api.py; the shapes below are theirs.
 */

// ------------------------------------------------------------------ shapes

export type ZuperSync = 'on' | 'paused' | 'off'
export type SetupState = 'pass' | 'fail' | 'confirm_by_hand'

export type ZuperSetupItem = {
  key: string
  title: string
  state: SetupState
  sentences: string[]
  confirmed: { by: string; at: string } | null
}

export type ZuperSetup = {
  checked_at: string | null
  passed: boolean
  results: ZuperSetupItem[]
}

export type ZuperHeartbeat = {
  last_sweep_started_at: string | null
  last_sweep_finished_at: string | null
  last_sweep_success_at: string | null
  sweep_cursor: string | null
  filter_checks: Record<string, boolean> | null
  last_push_at: string | null
  last_pull_at: string | null
  last_webhook_at: string | null
  last_error: string | null
  last_error_at: string | null
  last_counts: Record<string, number> | null
  last_digest_day: string | null
}

export type ZuperInboxRow = {
  id: number
  received_at: string | null
  event: string | null
  module: string | null
  record_uid: string | null
  status: string
  outcome: string | null
  error: string | null
  signature: string
}

export type ZuperStatus = {
  config: {
    env_enabled: boolean
    key_set: boolean
    base_url: string
    webhook_token_set: boolean
    webhook_secret_set: boolean
    crm_base_url: string
    requests_per_minute: number
    sweep_minutes: number
  }
  sync: ZuperSync
  enabled: boolean
  enabled_at: string | null
  workiz_cutover_date: string | null
  before_cutover: boolean
  setup: ZuperSetup
  blockers: string[]
  heartbeat: ZuperHeartbeat | null
  counts: Record<string, Record<string, number>>
  queue: {
    by_status: Record<string, number>
    recent_failures: { id: number; type: string; error: string | null; at: string | null }[]
  }
  inbox: { by_status: Record<string, number>; recent: ZuperInboxRow[] }
  load_report: ZuperLoadReport | null
  conflicts_total: number
  deletes_total: number
}

export type ZuperLoadReport = {
  mode?: string
  started_at?: string
  refused?: string
  stopped?: string
  phases?: Record<string, Record<string, number | number[]>>
  verification?: Record<string, unknown> & { mismatches?: number }
}

export type ZuperConflict = {
  id: number
  occurred_at: string | null
  crm_type: string
  crm_id: number | null
  zuper_uid: string | null
  field: string
  rule: string
  winner: string
  written_to: string
  before: unknown
  after: unknown
}

export type ZuperDelete = {
  id: number
  batch: string
  occurred_at: string | null
  direction: 'crm_to_zuper' | 'zuper_to_crm' | string
  crm_type: string
  crm_id: number
  zuper_uid: string | null
  state: string
  error: string | null
  label: string | null
  restored_at: string | null
  restore_detail: string | null
  restorable: boolean
}

export type ZuperPage<T> = { total: number; page: number; page_size: number; items: T[] }

export type ZuperRestoreResult = {
  batch: string
  results: { id: number; state: string; detail: string | null }[]
}

export type ZuperDocument = {
  id: string
  kind: 'quote' | 'invoice' | string
  number: string | null
  status: string | null
  total_cents: number | null
  balance_cents: number | null
  issued_on: string | null
  due_on: string | null
  updated_at: string | null
  opportunity_id: number | null
}

export type ZuperOpportunityMoney = {
  sync: ZuperSync
  linked: boolean
  last_synced_at: string | null
  quotes: ZuperDocument[]
  invoices: ZuperDocument[]
  value_source: 'invoices' | 'approved_quotes' | null
}

export type ZuperContactMoney = {
  sync: ZuperSync
  linked: boolean
  documents: (ZuperDocument & { opportunity_title: string | null })[]
}

export type ZuperAttachment = {
  id: string
  name: string | null
  content_type: string | null
  is_image: boolean
  created_at: string | null
  url: string
}

export type ZuperAttachments = {
  state: 'off' | 'not_linked' | 'unavailable' | 'ok'
  message: string | null
  attachments: ZuperAttachment[]
}

// ------------------------------------------------------------------ words

/** The rule names `app/zuper/engine.py` writes to the conflict log, in plain words. */
export const RULE_LABELS: Record<string, string> = {
  zuper_money: 'Zuper owns money',
  workiz_before_cutover: 'Workiz wins before cutover',
  zuper_schedule_after_cutover: 'Zuper owns the schedule after cutover',
  latest_edit: 'Latest edit wins',
  crm_owned: 'CRM-owned field',
  crm_cannot_hold: 'The CRM question cannot hold Zuper’s answer',
  invoice_paid: 'Invoice fully paid → Won',
  mirrored_cancel: 'Deleted in Zuper → visit cancelled',
}

export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? humanize(rule)
}

/** A field of the sync's view of a record (`app/zuper/mapping.py`). */
const FIELD_LABELS: Record<string, string> = {
  first_name: 'First name', last_name: 'Last name', email: 'Email', phone: 'Phone',
  company: 'Business name', source: 'Lead source', lead: 'Lead tag', title: 'Title',
  stage: 'Stage', status: 'Status', value: 'Value', owner_email: 'Owner',
  street: 'Address', city: 'Address', state: 'Address', zip: 'Address',
  starts_at: 'Start', ends_at: 'End', cancelled: 'Cancelled', body: 'Note',
  description: 'Description', done: 'Done', due_at: 'Due',
}

export function fieldLabel(field: string): string {
  if (field.startsWith('cl:')) return 'Checklist: ' + field.slice(3)
  if (field.startsWith('crm:')) return field.slice(4)
  return FIELD_LABELS[field] ?? humanize(field)
}

function humanize(key: string): string {
  const words = String(key ?? '').replace(/_/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : ''
}

export const CRM_TYPE_LABELS: Record<string, string> = {
  contact: 'Contact', opportunity: 'Opportunity', appointment: 'Visit', note: 'Note',
  task: 'Task', pipeline: 'Pipeline', stage: 'Stage',
}

export function crmTypeLabel(kind: string): string {
  return CRM_TYPE_LABELS[kind] ?? humanize(kind)
}

/** Rows and columns of the "Records by type" table. */
export const COUNT_ROWS: [string, string][] = [
  ['contact', 'Contacts'], ['opportunity', 'Opportunities'], ['appointment', 'Visits'],
  ['note', 'Notes'], ['task', 'Tasks'], ['pipeline', 'Pipelines'], ['stage', 'Stages'],
]
export const COUNT_COLUMNS: [string, string][] = [
  ['linked', 'Linked'], ['creating', 'Creating'], ['deleted', 'Deleted'],
]

/** The table, every cell a number (0 where the server sent none). */
export function countTable(counts: Record<string, Record<string, number>> | null | undefined) {
  return COUNT_ROWS.map(([key, label]) => ({
    key, label, cells: COUNT_COLUMNS.map(([state]) => counts?.[key]?.[state] ?? 0),
  }))
}

export function sideLabel(side: string): string {
  return side === 'crm' ? 'CRM' : side === 'zuper' ? 'Zuper' : side === 'workiz' ? 'Workiz'
    : humanize(side)
}

export const DIRECTION_LABELS: Record<string, string> = {
  crm_to_zuper: 'Deleted in the CRM → deleted in Zuper',
  zuper_to_crm: 'Deleted in Zuper → deleted in the CRM',
}

export function directionLabel(direction: string): string {
  return DIRECTION_LABELS[direction] ?? humanize(direction)
}

export const DELETE_STATE_LABELS: Record<string, string> = {
  pending: 'Waiting', mirrored: 'Mirrored', skipped: 'Skipped', failed: 'Failed',
  restored: 'Restored', restore_failed: 'Restore failed',
}

export function deleteStateLabel(state: string): string {
  return DELETE_STATE_LABELS[state] ?? humanize(state)
}

export type Tone = 'green' | 'red' | 'blue' | 'amber' | 'grey'

/** A chip's colours, GoHighLevel's badge palette. */
export const TONES: Record<Tone, { color: string; background: string }> = {
  green: { color: 'rgb(2,122,72)', background: 'rgb(236,253,243)' },
  red: { color: 'rgb(180,35,24)', background: 'rgb(254,243,242)' },
  blue: { color: 'rgb(23,92,211)', background: 'rgb(239,248,255)' },
  amber: { color: 'rgb(181,71,8)', background: 'rgb(255,250,235)' },
  grey: { color: 'rgb(52,64,84)', background: 'rgb(242,244,247)' },
}

export function syncChip(sync: string): { text: string; tone: Tone } {
  if (sync === 'on') return { text: 'On', tone: 'green' }
  if (sync === 'paused') return { text: 'Paused', tone: 'amber' }
  return { text: 'Off', tone: 'grey' }
}

export function setupChip(state: string): { text: string; tone: Tone } {
  if (state === 'pass') return { text: 'PASS', tone: 'green' }
  if (state === 'fail') return { text: 'FAIL', tone: 'red' }
  return { text: 'Confirm by hand', tone: 'amber' }
}

/** "PARTIALLY_PAID" → "Partially paid", with a tone for the chip. */
export function documentStatus(status: string | null | undefined): { text: string; tone: Tone } {
  const raw = String(status ?? '').trim()
  if (!raw) return { text: 'Unknown', tone: 'grey' }
  const upper = raw.toUpperCase()
  const text = humanize(raw.toLowerCase())
  if (['PAID', 'APPROVED', 'ACCEPTED'].includes(upper)) return { text, tone: 'green' }
  if (['DECLINED', 'REJECTED', 'VOID', 'VOIDED', 'OVERDUE', 'CANCELED', 'CANCELLED']
    .includes(upper)) return { text, tone: 'red' }
  if (upper.includes('PARTIAL')) return { text, tone: 'amber' }
  if (['DRAFT', 'EXPIRED'].includes(upper)) return { text, tone: 'grey' }
  return { text, tone: 'blue' }
}

export function kindLabel(kind: string): string {
  return kind === 'quote' ? 'Quote' : kind === 'invoice' ? 'Invoice' : humanize(kind)
}

export function valueSourceSentence(source: string | null | undefined): string | null {
  if (source === 'invoices') return 'The card’s value follows the invoices’ total in Zuper.'
  if (source === 'approved_quotes') {
    return 'The card’s value follows the approved quotes’ total in Zuper.'
  }
  return null
}

export function filterCheckLabel(narrows: boolean): string {
  return narrows ? 'narrows' : 'ignored — paged whole'
}

export function cutoverSentence(date: string | null | undefined, beforeCutover: boolean): string {
  if (!date) return 'Workiz is still in use'
  if (beforeCutover) return `Workiz is still in use until ${dateText(date)}`
  return `Workiz retired on ${dateText(date)} — the importer refuses to run`
}

// ------------------------------------------------------------------ numbers and times

/** "$1,234.50" from cents; "—" when Zuper sent none. */
export function money(cents: number | null | undefined): string {
  if (cents == null || !Number.isFinite(cents)) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
    .format(cents / 100)
}

/** The account's zone: every time on these pages reads as it does in Bradenton. */
export const ACCOUNT_TIME_ZONE = 'America/New_York'

/** "Sep 16 2026, 9:17 AM (EDT)"; "—" for none or garbage. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const parts = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
    timeZoneName: 'short', timeZone: ACCOUNT_TIME_ZONE,
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('month')} ${get('day')} ${get('year')}, ${get('hour')}:${get('minute')} `
    + `${get('dayPeriod').toUpperCase()} (${get('timeZoneName')})`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov',
  'Dec']

/** A calendar date as Zuper wrote it ("2026-09-10", or the date part of a timestamp) →
    "Sep 10, 2026". Never shifted by a zone: a date is not an instant. */
export function dateText(value: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ''))
  if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return value ? String(value) : '—'
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`
}

/** A conflict's before / after as short text. The job value is cents, so it reads as money. */
export function valueText(value: unknown, field = ''): string {
  if (value === null || value === undefined || value === '') return '—'
  if (field === 'value' && typeof value === 'number') return money(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  const text = typeof value === 'string' ? value : typeof value === 'number' ? String(value)
    : JSON.stringify(value)
  return text.length > 120 ? text.slice(0, 119) + '…' : text
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil((total || 0) / Math.max(1, pageSize)))
}

// ------------------------------------------------------------------ what is drawn

/** The modal's "Quotes & invoices" nav item: only for a job linked to Zuper, or one that
    has a document anyway. No answer (a 404, still loading) is no item. */
export function showMoneyNav(data: ZuperOpportunityMoney | null | undefined): boolean {
  return !!data && (data.linked === true
    || (data.quotes?.length ?? 0) + (data.invoices?.length ?? 0) > 0)
}

/** The contact panel's section: only when there is at least one document. */
export function showContactMoney(data: ZuperContactMoney | null | undefined): boolean {
  return !!data && (data.documents?.length ?? 0) > 0
}

/** The Photos tab's Zuper part: a group, a one-line message, or nothing at all. */
export function attachmentsView(
  data: ZuperAttachments | null | undefined,
): 'group' | 'message' | 'none' {
  if (!data) return 'none'
  if (data.state === 'ok' && data.attachments.length > 0) return 'group'
  if (data.state === 'unavailable') return 'message'
  return 'none'
}

/** The toggle is drawn only when it can work: on (to pause), or off with nothing in the way.
    Otherwise the page lists why instead of drawing a dead switch. */
export function switchView(status: Pick<ZuperStatus, 'enabled' | 'blockers'>):
  'pause' | 'switch_on' | 'blocked' {
  if (status.enabled) return 'pause'
  return status.blockers.length === 0 ? 'switch_on' : 'blocked'
}

/** The initial load's report, as rows of counts and the mismatched ids. Ids only — the
    report never carries a name. */
export function loadSummary(report: ZuperLoadReport | null | undefined) {
  if (!report) return null
  const phases = Object.entries(report.phases ?? {}).map(([kind, counts]) => ({
    kind: crmTypeLabel(kind),
    counts: Object.entries(counts)
      .filter(([, v]) => typeof v === 'number')
      .map(([k, v]) => `${humanize(k).toLowerCase()} ${v}`).join(' · '),
    failedIds: Array.isArray(counts.failed_ids) ? counts.failed_ids as number[] : [],
  }))
  const mismatches: { kind: string; what: string; ids: (string | number)[] }[] = []
  const v = report.verification ?? {}
  for (const [kind, entry] of Object.entries(v)) {
    if (!entry || typeof entry !== 'object') continue
    for (const [key, what] of [['crm_not_mapped', 'In the CRM, not linked'],
      ['mapped_not_in_zuper', 'Linked, missing in Zuper'],
      ['zuper_not_mapped', 'In Zuper, not linked']] as const) {
      const ids = (entry as Record<string, unknown>)[key]
      if (Array.isArray(ids) && ids.length) mismatches.push({ kind: crmTypeLabel(kind), what, ids })
    }
  }
  return {
    mode: report.mode === 'commit' ? 'Commit' : report.mode === 'dry-run' ? 'Dry run' : '—',
    startedAt: report.started_at ?? null,
    refused: report.refused ?? null,
    stopped: report.stopped ?? null,
    phases,
    mismatchTotal: typeof v.mismatches === 'number' ? v.mismatches : null,
    mismatches,
  }
}

// ------------------------------------------------------------------ deep links

/**
 * The "CRM Link" the sync writes into Zuper (`mapping.crm_link`):
 * /opportunities?opportunity=<id> and /contacts?contact=<id>. Which record a fresh page load
 * should open, or null. Only a positive whole number is an id.
 */
export function recordFromLocation(pathname: string, search: string):
  { view: 'opportunities' | 'contacts'; id: number } | null {
  const path = String(pathname ?? '').replace(/\/+$/, '').toLowerCase()
  const params = new URLSearchParams(search ?? '')
  const idOf = (raw: string | null) => (raw && /^\d+$/.test(raw) && Number(raw) > 0
    ? Number(raw) : null)
  if (path === '/opportunities') {
    const id = idOf(params.get('opportunity'))
    return id ? { view: 'opportunities', id } : null
  }
  if (path === '/contacts') {
    const id = idOf(params.get('contact'))
    return id ? { view: 'contacts', id } : null
  }
  return null
}

/** The same URL without the record parameter, so a reload after closing the record does not
    open it again. Other parameters (?pipeline=) are kept. */
export function withoutRecordParam(pathname: string, search: string): string {
  const params = new URLSearchParams(search ?? '')
  params.delete('opportunity')
  params.delete('contact')
  const rest = params.toString()
  return (pathname || '/') + (rest ? '?' + rest : '')
}
