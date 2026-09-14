/**
 * The top-bar status dot, with no React in it.
 *
 * IMPORT-FREE on purpose (the one `import type` is erased), like `pageTabs.ts`:
 * `backend/tests/test_connection_status_ui.py` runs this exact file under node, so
 * which colour each state is, "the dot shows the worst", and every sentence the hover
 * card says are asserted by executing the shipped code.
 *
 * The owner (2026-09-14): green = everything working · amber = connecting / degraded ·
 * red = something is broken · grey = the browser phone is switched off · a distinct live
 * state while a call is in progress. The dot is the WORST of three checks:
 *
 *   1. this browser's phone — live in the browser (`lib/softphone.ts`);
 *   2. the link to the phone system  } from GET /api/connection-status, which asks
 *   3. Quo sync                      } owen-main server-side (backend/app/connection_status.py)
 */
import type { CallPhase, SoftphoneStatus } from './softphone'

/** What the backend says about a check. */
export type CheckState = 'ok' | 'degraded' | 'down' | 'off' | 'unknown'
export type Tone = 'green' | 'amber' | 'red' | 'grey'

export type QuoFacts = {
  mirror_enabled: boolean
  poll_seconds: number
  last_tick_at: string | null
  last_tick_ran: boolean | null
  tick_stale: boolean
  backfill_days: number
  backfill_completed_at: string | null
  webhook_enabled: boolean
  last_webhook_at: string | null
}

export type ServerStatus = {
  checked_at: string
  link: { state: CheckState; sentence: string }
  quo: { state: CheckState; sentence: string; facts: QuoFacts | null }
}

/** One row of the hover card. */
export type Check = {
  key: 'phone' | 'link' | 'quo'
  title: string
  tone: Tone
  /** The one word at the right of the row. */
  word: string
  sentence: string
  /** Smaller lines under the sentence ("Registered as …", "Last check · 2 min ago"). */
  details: string[]
}

/** The dot's colours. Green/amber/red/grey are the softphone dock's own, unchanged. */
export const TONE_COLOR: Record<Tone, string> = {
  green: 'rgb(23,124,73)',
  amber: 'rgb(181,102,10)',
  red: 'rgb(180,35,24)',
  grey: 'rgb(152,162,179)',
}

/** Worst first. Grey outranks green: "the phone is off" must not read as "all good". */
const RANK: Record<Tone, number> = { red: 3, amber: 2, grey: 1, green: 0 }

export const WORD: Record<CheckState, string> = {
  ok: 'Working',
  degraded: 'Degraded',
  down: 'Not working',
  off: 'Off',
  unknown: 'Unknown',
}

export function stateTone(state: CheckState): Tone {
  switch (state) {
    case 'ok':
      return 'green'
    case 'degraded':
      return 'amber'
    case 'down':
      return 'red'
    default:
      // `off` is a switch somebody chose; `unknown` is a check that could not be asked
      // because an EARLIER hop failed, and that hop is already red on its own row.
      return 'grey'
  }
}

export function worst(tones: Tone[]): Tone {
  return tones.reduce<Tone>((w, t) => (RANK[t] > RANK[w] ? t : w), 'green')
}

/** "just now", "4 min ago", "3 h ago", "2 days ago". Never a clock time: whose clock? */
export function ago(iso: string | null | undefined, nowMs: number): string | null {
  if (!iso) return null
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  const s = Math.max(0, Math.round((nowMs - t) / 1000))
  if (s < 45) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.round(h / 24)} days ago`
}

/** Check 1 — this browser's phone, from the softphone hook's own state. */
export function phoneCheck(p: {
  status: SoftphoneStatus
  operator: string | null
  error: string | null
}): Check {
  const row = (tone: Tone, word: string, sentence: string, details: string[] = []): Check => ({
    key: 'phone',
    title: "This browser's phone",
    tone,
    word,
    sentence,
    details: p.error ? [...details, p.error] : details,
  })
  switch (p.status) {
    case 'ready':
      return row('green', 'Ready', 'This browser rings alongside the mobiles.',
        p.operator ? [`Registered as ${p.operator}`] : [])
    case 'connecting':
      return row('amber', 'Connecting', 'Not taking calls yet.')
    case 'reconnecting':
      return row('amber', 'Reconnecting', 'NOT taking calls right now.')
    case 'failed':
      return row('red', 'Not registered', 'This browser cannot take calls.')
    case 'off':
      return row('grey', 'Off', 'Switched off. Calls ring the mobiles only.')
    case 'elsewhere':
      return row('grey', 'Other tab', 'Another tab is the phone. Only one tab can be.')
    case 'no-operator':
      return row('grey', 'Not set up', 'Your account is not set up to take calls in the browser.')
    case 'unavailable':
    default:
      return row('grey', 'Not set up', 'Calls in the browser are not set up on this deployment.')
  }
}

/**
 * Checks 2 and 3 from the server's answer. `server` null with `failed` true means the CRM
 * itself did not answer — which is red, because then nothing here is working.
 */
export function serverChecks(
  server: ServerStatus | null,
  opts: { failed: boolean; offline: boolean; nowMs: number },
): Check[] {
  if (opts.offline || opts.failed || !server) {
    const sentence = opts.offline
      ? 'This computer is offline.'
      : opts.failed
        ? "Can't reach the CRM server."
        : 'Checking…'
    const tone: Tone = opts.offline || opts.failed ? 'red' : 'grey'
    return [
      { key: 'link', title: 'Link to the phone system', tone, word: tone === 'red' ? 'Not working' : 'Checking',
        sentence, details: [] },
      { key: 'quo', title: 'Quo sync', tone: 'grey', word: 'Unknown',
        sentence: tone === 'red' ? 'Unknown until the CRM answers.' : 'Checking…', details: [] },
    ]
  }
  return [
    {
      key: 'link',
      title: 'Link to the phone system',
      tone: stateTone(server.link.state),
      word: WORD[server.link.state],
      sentence: server.link.sentence,
      details: [],
    },
    {
      key: 'quo',
      title: 'Quo sync',
      tone: stateTone(server.quo.state),
      word: WORD[server.quo.state],
      sentence: server.quo.sentence,
      details: quoDetails(server.quo.facts, opts.nowMs),
    },
  ]
}

/** The facts under the Quo row. Only what is known is said; nothing is guessed. */
export function quoDetails(f: QuoFacts | null, nowMs: number): string[] {
  if (!f || !f.mirror_enabled) return []
  const out: string[] = []
  const tick = ago(f.last_tick_at, nowMs)
  out.push(tick
    ? `Last check ${tick}${f.tick_stale ? ' (overdue)' : ''}${f.last_tick_ran === false ? ', did not run' : ''}`
    : 'No check recorded yet')
  const backfill = ago(f.backfill_completed_at, nowMs)
  out.push(backfill
    ? `${f.backfill_days}-day history imported ${backfill}`
    : `${f.backfill_days}-day history not finished importing`)
  if (f.webhook_enabled) {
    const hook = ago(f.last_webhook_at, nowMs)
    out.push(hook ? `Webhook on, last delivery ${hook}` : 'Webhook on, no delivery yet')
  } else {
    out.push('Webhook off')
  }
  return out
}

export type Overall = {
  /** `live` while a call is in progress, whatever the checks say. */
  tone: Tone | 'live'
  headline: string
}

export function overall(checks: Check[], phase: CallPhase): Overall {
  if (phase === 'in-call') return { tone: 'live', headline: 'On a call' }
  const tone = worst(checks.map((c) => c.tone))
  const headline =
    tone === 'green'
      ? 'Everything is working'
      : tone === 'red'
        ? 'Something is not working'
        : tone === 'amber'
          ? 'Something needs attention'
          : checks.find((c) => c.key === 'phone')?.tone === 'grey'
            ? 'Browser phone is off'
            : 'Not everything is on'
  return { tone, headline }
}

/**
 * The action the phone row offers. Exactly the floating dock's, which this replaces:
 * Switch off only while ready, Switch on while off, Try again after a failure, Use this
 * tab when another tab holds the phone. Nothing otherwise.
 */
export function phoneAction(status: SoftphoneStatus): { label: string; online: boolean } | null {
  switch (status) {
    case 'ready':
      return { label: 'Switch off', online: false }
    case 'off':
      return { label: 'Switch on', online: true }
    case 'failed':
      return { label: 'Try again', online: true }
    case 'elsewhere':
      return { label: 'Use this tab', online: true }
    default:
      return null
  }
}
