/**
 * The number this CRM calls and texts FROM (2026-09-16).
 *
 * Imports nothing, so node executes it in `backend/tests/test_our_line.py`.
 *
 * ## Why this file exists
 *
 * The line was hard-coded in FIVE places — `lib/dialPad.ts`, `pages/ConversationsPage.tsx`,
 * `components/AiConnectionsSettings.tsx`, `components/ai/AgentBuilder.tsx` and the backend —
 * and on 2026-09-16 the owner changed it: the CRM moved from `+19544829099` to
 * `+19547758492`, and the old number was fully unbound. Five copies is five chances for a
 * screen to keep telling a customer to reply to a number that no longer reaches anybody.
 *
 * So the backend, which is the only thing that actually knows (`app/crmlink.py`, overridden
 * by `CRM_LINK_FROM_NUMBER`), now reports it on `GET /api/connection-status` — an endpoint
 * every signed-in page already polls for the status dot. The browser reads it from there.
 *
 * ## The fallback is not the answer, it is the last resort
 *
 * `FALLBACK_LINE` is used only before the first status poll answers, or if it never does.
 * It is deliberately the CURRENT number, so a cold render says the right thing; but a
 * deployment whose `CRM_LINK_FROM_NUMBER` differs is correct within one poll, and this
 * constant is never what a send actually uses — the server picks the line, not the browser.
 *
 * ## History keeps its own number
 *
 * Nothing here relabels an old event. A thread row carrying `source_number:
 * +19544829099` was genuinely sent on that line and still says so: the source chip reads
 * the event's own number, never this one. A number is a fact about when something
 * happened, not a setting.
 */

/** The current line, for a render that has not heard from the server yet. */
export const FALLBACK_LINE = '+19547758492'

/**
 * The line to show and to compare against.
 *
 * Takes whatever `GET /api/connection-status` last reported — `undefined` while the query
 * is in flight, `null` if it failed — and never returns empty.
 */
export function ourLine(configured?: string | null): string {
  const line = String(configured ?? '').trim()
  return line || FALLBACK_LINE
}

/** The last ten digits — the identity rule the backend, the CRM link and owen-main all
 *  share. Two systems disagreeing about whether two renderings are one line is how a text
 *  ends up addressed to ourselves. */
export function lineKey(value?: string | null): string {
  const digits = String(value ?? '').replace(/\D/g, '')
  return digits.length >= 10 ? digits.slice(-10) : digits
}

/**
 * Is this number our own line?
 *
 * The check behind "That is this CRM's own number — it cannot text itself." It follows the
 * CONFIGURED line, so when the owner changes the number the refusal moves with it — and,
 * just as importantly, the OLD number stops being refused: `+19544829099` is somebody
 * else's line now, and there is no reason this CRM may not text it.
 */
export function isOurLine(value: string | null | undefined, configured?: string | null): boolean {
  const key = lineKey(value)
  return key.length === 10 && key === lineKey(ourLine(configured))
}
