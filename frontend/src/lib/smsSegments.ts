/**
 * How long a text is, the way a carrier counts it — "New message" and the composer's
 * character / segment count (2026-09-15).
 *
 * Imports nothing, so node executes it in `backend/tests/test_new_message_ui.py`.
 *
 * A text is billed and delivered in SEGMENTS. Written only in the GSM-7 alphabet a
 * segment holds 160 characters (153 each once it is split, the rest is the header that
 * glues the parts back together); the extension characters `^{}\[~]|€` take two. One
 * character outside GSM-7 — an emoji, a curly quote pasted from Word, an accented
 * capital — switches the WHOLE message to UCS-2: 70 per segment, 67 once split,
 * counted in UTF-16 code units (an emoji is two).
 */

const GSM_BASIC =
  '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?' +
  '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà'
const GSM_EXTENDED = '^{}\\[~]|€\f'

export type SegmentInfo = {
  /** What a person would call the length: characters typed (an emoji is one). */
  characters: number
  /** 'GSM-7' or 'UCS-2' — which alphabet the whole text will be sent in. */
  encoding: 'GSM-7' | 'UCS-2'
  /** Units the carrier counts: septets for GSM-7, UTF-16 code units for UCS-2. */
  units: number
  segments: number
  /** Units per segment at the current length (160/153 or 70/67). */
  perSegment: number
  /** Units left before the next segment starts. */
  remaining: number
}

export function segmentInfo(body: string): SegmentInfo {
  const text = String(body ?? '')
  const characters = Array.from(text).length
  let septets = 0
  let gsm = true
  for (const ch of text) {
    if (GSM_BASIC.includes(ch)) septets += 1
    else if (GSM_EXTENDED.includes(ch)) septets += 2
    else { gsm = false; break }
  }
  if (gsm) {
    const segments = septets === 0 ? 0 : septets <= 160 ? 1 : Math.ceil(septets / 153)
    const perSegment = septets <= 160 ? 160 : 153
    const remaining = segments <= 1 ? 160 - septets : segments * 153 - septets
    return { characters, encoding: 'GSM-7', units: septets, segments, perSegment, remaining }
  }
  const units = text.length
  const segments = units <= 70 ? 1 : Math.ceil(units / 67)
  const perSegment = units <= 70 ? 70 : 67
  const remaining = segments <= 1 ? 70 - units : segments * 67 - units
  return { characters, encoding: 'UCS-2', units, segments, perSegment, remaining }
}

/** The one line under the message box: "42 characters · 1 segment". */
export function segmentLabel(body: string): string {
  const s = segmentInfo(body)
  const chars = `${s.characters} character${s.characters === 1 ? '' : 's'}`
  if (s.segments === 0) return chars
  const segs = `${s.segments} segment${s.segments === 1 ? '' : 's'}`
  return s.encoding === 'UCS-2'
    ? `${chars} · ${segs} · special characters: ${s.perSegment} per segment`
    : `${chars} · ${segs}`
}
