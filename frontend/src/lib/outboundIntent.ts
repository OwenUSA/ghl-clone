/**
 * "This browser just asked for a call" — so the INVITE that follows is ours to answer.
 *
 * Ported from owen-main (`frontend/src/lib/outboundIntent.ts`), for the same reason it
 * exists there: owen-main places an outbound call by ringing the operator's OWN browser
 * phone first, and that leg arrives as an ordinary incoming INVITE whose caller-ID is
 * the number we dialled. From the INVITE alone, "my own call connecting" and "that
 * customer is calling me" are indistinguishable. The only reliable signal is that this
 * tab asked for a call a moment ago.
 *
 * Kept from owen-main:
 *   - module scope, marked at ONE choke point (`lib/callLauncher.ts`) before the request
 *     leaves, so no call site can forget it;
 *   - TTL-bounded, so a genuine call minutes later is never auto-answered;
 *   - SINGLE-SHOT: claiming clears it, so one request answers at most one INVITE.
 *
 * One thing added: the claim must MATCH THE NUMBER DIALLED (last ten digits). owen-main
 * claims whatever INVITE arrives first; here a real customer who happens to ring during
 * those seconds is not answered as if they were our own call — their INVITE does not
 * match, is shown as an incoming call, and leaves the intent in place for the leg that
 * does. An INVITE with no readable caller-ID is never claimed.
 *
 * Imports nothing, so node executes it in the tests. `now` is injectable for the same
 * reason.
 */

/** How long after asking for a call an arriving INVITE may be our own leg. owen-main's
 *  value: long enough for its queue and the originate, short enough that a genuine call
 *  a minute later is treated as inbound. */
export const OUTBOUND_INTENT_TTL_MS = 45_000

type Intent = { digits: string; at: number }

let pending: Intent | null = null

function lastTen(number: string | null | undefined): string {
  const d = String(number ?? '').replace(/\D/g, '')
  return d.length >= 10 ? d.slice(-10) : ''
}

/** Called the moment a call is requested, before the request is sent. */
export function markOutboundIntent(number: string, now: number = Date.now()): void {
  const digits = lastTen(number)
  pending = digits ? { digits, at: now } : null
}

/**
 * Is this INVITE our own outbound leg? True at most ONCE per mark, only within the TTL,
 * and only for the number that was dialled. A true answer consumes the intent; a
 * non-matching INVITE leaves it for the leg still on its way. An expired intent is
 * dropped whatever arrives.
 */
export function claimOutboundIntent(peer: string | null | undefined,
  now: number = Date.now()): boolean {
  if (!pending) return false
  if (now - pending.at >= OUTBOUND_INTENT_TTL_MS || now < pending.at) {
    pending = null
    return false
  }
  const digits = lastTen(peer)
  if (!digits || digits !== pending.digits) return false
  pending = null
  return true
}

/** The request was refused or failed — no INVITE is coming, so nothing may be claimed. */
export function clearOutboundIntent(): void {
  pending = null
}

/** Is a call still on its way to this browser? For the dialer's "Calling…" state. */
export function outboundIntentPending(now: number = Date.now()): boolean {
  return pending !== null && now - pending.at < OUTBOUND_INTENT_TTL_MS
}
