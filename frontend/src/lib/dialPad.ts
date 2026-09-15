/**
 * The Conversations dialer's number field: what typing, pasting and the keypad do to it,
 * and why Call is (or is not) allowed.
 *
 * Imports nothing, so node executes it in `backend/tests/test_dialer_ui.py`.
 *
 * The rules are the server's (`dial_problem` in `backend/app/main.py`), written again
 * here only so the reason can be shown BEFORE Call is pressed. The server is the gate;
 * this is the explanation. The sentences are the same words on both sides.
 *
 * North American numbers only: the one line this CRM calls from is a US BulkVS DID.
 */

/** The number every call leaves from. There is no other line — Quo cannot place calls. */
export const CALLING_FROM = '+19544829099'

/** The keypad, in phone order. The letters are what a phone prints under each digit. */
export const KEYPAD: { key: string; letters: string }[] = [
  { key: '1', letters: '' }, { key: '2', letters: 'ABC' }, { key: '3', letters: 'DEF' },
  { key: '4', letters: 'GHI' }, { key: '5', letters: 'JKL' }, { key: '6', letters: 'MNO' },
  { key: '7', letters: 'PQRS' }, { key: '8', letters: 'TUV' }, { key: '9', letters: 'WXYZ' },
  { key: '*', letters: '' }, { key: '0', letters: '+' }, { key: '#', letters: '' },
]

/** The characters the field keeps: digits, star, pound and a leading +. */
function keep(raw: string): string {
  const text = String(raw ?? '')
  const plus = text.trimStart().startsWith('+') ? '+' : ''
  return plus + text.replace(/[^\d*#]/g, '')
}

/**
 * What the field shows for whatever was typed: formatted as a US number while it can be
 * one — `(941) 555-01` — and left as typed once it cannot (a star, a pound, too many
 * digits), so the operator sees exactly what they entered next to the reason it fails.
 */
export function formatDialInput(raw: string): string {
  const kept = keep(raw)
  if (/[*#]/.test(kept)) return kept
  let d = kept.replace(/\D/g, '')
  const plus = kept.startsWith('+')
  let prefix = ''
  if (d.length > 10 && d.startsWith('1')) {
    prefix = plus ? '+1 ' : '1 '
    d = d.slice(1)
  } else if (plus) {
    // A + that is not +1: leave it alone, the reason under the field explains it.
    return kept
  }
  if (d.length > 10) return prefix + d
  if (d.length === 0) return prefix.trim()
  if (d.length <= 3) return `${prefix}(${d}`
  if (d.length <= 6) return `${prefix}(${d.slice(0, 3)}) ${d.slice(3)}`
  return `${prefix}(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`
}

/** A keypad press. Appends and re-formats; returns the new field value. */
export function pressKey(value: string, key: string): string {
  if (!/^[\d*#]$/.test(key)) return value
  return formatDialInput(keep(value) + key)
}

/** Backspace removes the last thing that was TYPED, not the last formatting character. */
export function backspace(value: string): string {
  const kept = keep(value)
  return formatDialInput(kept.slice(0, -1))
}

/**
 * What a paste becomes. People paste `tel:+19415550199`, `+1 (941) 555-0199`,
 * `941.555.0199` and "Call me on 941-555-0199 after 5" — the digits are what they meant.
 * A paste REPLACES what was there: pasting a number into a half-typed one and getting
 * the two glued together is never what anybody wanted.
 */
export function pasteNumber(text: string): string {
  const raw = String(text ?? '').replace(/^\s*tel:/i, '')
  const match = raw.match(/\+?\d[\d\s().\-*#]{6,}\d|\+?\d{7,}/)
  return formatDialInput(match ? match[0] : raw)
}

/** `+1XXXXXXXXXX` for a dialable number, else null. What the server is sent. */
export function normaliseNumber(value: string): string | null {
  return dialProblem(value) === null ? '+1' + nationalDigits(value) : null
}

function nationalDigits(value: string): string {
  const d = keep(value).replace(/\D/g, '')
  return d.length === 11 && d.startsWith('1') ? d.slice(1) : d
}

/**
 * Why this number cannot be called, or null when it can. The same rules and words as
 * the server's `dial_problem`.
 */
export function dialProblem(value: string): string | null {
  const kept = keep(value)
  if (!kept.replace('+', '')) return 'Enter a number to call — 10 digits, area code first.'
  if (/[*#]/.test(kept)) {
    return 'A number to call has only digits. Star and pound are for menus once the call connects.'
  }
  const all = kept.replace(/\D/g, '')
  if (kept.startsWith('+') && !all.startsWith('1')) {
    return 'Only US and Canadian numbers can be called from here — enter 10 digits, area code first.'
  }
  const d = nationalDigits(value)
  if (d.length > 10) {
    return 'Only US and Canadian numbers can be called from here — enter 10 digits, area code first.'
  }
  if (d.length < 10) return 'That number is too short — enter all 10 digits, area code first.'
  if ('01'.includes(d[0]) || '01'.includes(d[3])) {
    return 'That is not a valid US number — an area code and an exchange cannot start with 0 or 1.'
  }
  if (d === CALLING_FROM.slice(2)) return "That is this CRM's own number — it cannot call itself."
  return null
}

/**
 * "New message" (2026-09-15): why this number cannot be TEXTED, or null when it can.
 * The dialer's rules — the same one line, North American numbers only — in words about
 * texting. The server's twin is `text_problem` in `backend/app/main.py`; a test runs
 * both on the same inputs.
 */
export function textProblem(value: string): string | null {
  const kept = keep(value)
  if (!kept.replace('+', '')) return 'Enter a number to text — 10 digits, area code first.'
  if (/[*#]/.test(kept)) return 'A number to text has only digits.'
  const all = kept.replace(/\D/g, '')
  if (kept.startsWith('+') && !all.startsWith('1')) {
    return 'Only US and Canadian numbers can be texted from here — enter 10 digits, area code first.'
  }
  const d = nationalDigits(value)
  if (d.length > 10) {
    return 'Only US and Canadian numbers can be texted from here — enter 10 digits, area code first.'
  }
  if (d.length < 10) return 'That number is too short — enter all 10 digits, area code first.'
  if ('01'.includes(d[0]) || '01'.includes(d[3])) {
    return 'That is not a valid US number — an area code and an exchange cannot start with 0 or 1.'
  }
  if (d === CALLING_FROM.slice(2)) return "That is this CRM's own number — it cannot text itself."
  return null
}

/** `+1XXXXXXXXXX` for a textable number, else null. What the server is sent. */
export function normaliseTextNumber(value: string): string | null {
  return textProblem(value) === null ? '+1' + nationalDigits(value) : null
}
