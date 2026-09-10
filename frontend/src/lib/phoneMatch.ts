/**
 * "Is this a phone number, and is it the same phone number?" — in the browser.
 *
 * The authority on contact search is the server: `/api/contacts?q=` matches the
 * last ten digits of a stored number against the last ten of the query
 * (`backend/app/phone_match.py`). Nothing here re-implements that search, and
 * the picker never filters the server's results by re-running the rule.
 *
 * The browser needs the same rule for two decisions the server is not asked to
 * make, both of which happen before anything is created:
 *
 *  - **Where does a prefill go?** A dispatcher who typed `8135550102` into the
 *    picker and found nobody should get that number in the phone field of the
 *    new-contact form, not in the first-name field. Typing it twice is exactly
 *    the friction the `+` exists to remove.
 *  - **Is this number already on file?** The duplicate warning asks the server
 *    for candidates, then has to say whether a candidate is really the same
 *    line or merely a text match on something else. That is a comparison of two
 *    strings the browser already holds.
 *
 * The constants below are the same ones the backend module declares, and the
 * two are pinned to each other by `backend/tests/test_contact_picker.py`. If
 * they ever drift, the browser warns about duplicates the server would not find
 * — which is worse than not warning at all.
 *
 * This module imports nothing on purpose, so node can execute it directly in
 * the tests, the way `calendarGrid.ts` is executed (see DECISIONS.md).
 */

/** A national number: the country code in front of it is what we ignore. */
export const NATIONAL_DIGITS = 10

/** Below this a number is noise, not a search. Four digits is what a person
 *  reads off a caller ID: "the customer ending 0102". */
export const MIN_MATCH_DIGITS = 4

/** Everything people put around the digits of a number. */
const PHONE_SHAPED = /^[0-9 ()\-./+]+$/

/** Every digit in `raw`, in order: `"(813) 555-0102"` -> `"8135550102"`. */
export function digits(raw: string | null | undefined): string {
  return (raw ?? '').replace(/\D/g, '')
}

/**
 * Is what was typed a phone number rather than a name or a business?
 *
 * Digits and phone punctuation only, and at least `MIN_MATCH_DIGITS` of them.
 * `Maria` is a name, `24/7` is a business, `813-555-0102` is a number.
 */
export function looksLikePhone(q: string | null | undefined): boolean {
  const text = (q ?? '').trim()
  if (!text || !PHONE_SHAPED.test(text)) return false
  return digits(text).length >= MIN_MATCH_DIGITS
}

/**
 * Do these two numbers identify the same line?
 *
 * The last ten digits, both sides, so `(813) 555-0102` and `+18135550102` are
 * one number. A blank is never "the same" as anything, including another blank
 * — two contacts with no phone are not duplicates of each other.
 */
export function sameNumber(a: string | null | undefined, b: string | null | undefined): boolean {
  const da = digits(a)
  const db = digits(b)
  if (!da || !db) return false
  return da.slice(-NATIONAL_DIGITS) === db.slice(-NATIONAL_DIGITS)
}

/**
 * Split what was typed into the fields of the new-contact form.
 *
 * A number goes to `phone`. Anything else is a name, split on the first space
 * — "Maria Alvarez" is a first and a last name, and re-typing either of them
 * is the friction this whole feature exists to remove. A single word is a first
 * name: it is far more often "Maria" than "Alvarez".
 */
export function prefillFrom(typed: string): { first_name?: string; last_name?: string; phone?: string } {
  const text = typed.trim()
  if (!text) return {}
  if (looksLikePhone(text)) return { phone: text }
  const cut = text.indexOf(' ')
  if (cut < 0) return { first_name: text }
  return { first_name: text.slice(0, cut), last_name: text.slice(cut + 1).trim() }
}
