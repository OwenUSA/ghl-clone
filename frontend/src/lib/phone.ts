/**
 * Rendering a phone number for a person to read.
 *
 * Lifted out of `softphoneApi.ts`, where it originally lived because the incoming-call
 * card was the first thing that needed it. Nothing about formatting a number is a
 * softphone concept, and two unrelated screens now want it: the call card, and the
 * source chip on a conversation thread, which names the LINE an event came through.
 *
 * The move is not cosmetic. `test_softphone_ui.py` fences pages against owning the
 * softphone by matching the word "softphone" in their source, and that fence has
 * already been bitten once by vocabulary rather than behaviour (a page explaining in a
 * comment that it does NOT use the softphone). A conversations page importing a
 * formatter from a file called `softphoneApi` would have tripped it for the same
 * reason — correct code failing a fence, which is how fences get widened or deleted.
 */

/**
 * A phone number as a person reads it. Falls back to exactly what arrived, because a
 * caller-ID that is not a NANP number (an international caller, `anonymous`) still has
 * to be shown -- the one thing the card must never do is render nothing.
 */
export function formatPhone(raw: string | null | undefined): string {
  const text = String(raw || '').trim()
  const d = text.replace(/\D/g, '')
  if (d.length === 11 && d.startsWith('1')) {
    return `(${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7)}`
  }
  if (d.length === 10) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`
  return text
}
