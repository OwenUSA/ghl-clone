/**
 * CSV for the Opportunities export.
 *
 * Written free of React and of any import, like `calendarGrid.ts`, so node can
 * execute the real shipped module — see `backend/tests/test_csv_export.py`. "The
 * export contains exactly the selected rows" is a claim a regex over the page
 * source cannot check, and an export that quietly drops or duplicates a row looks
 * completely fine until someone reconciles it against the board.
 *
 * Nothing here touches the DOM: turning a string into a download is the caller's
 * job (`downloadCsv` in BulkActionsBar.tsx).
 */

/**
 * Integer cents -> a plain decimal string, without ever becoming a float.
 *
 * `(cents / 100).toFixed(2)` is the obvious version and it is the same mistake
 * `centsFromDollars` exists to avoid, in the other direction. This is also
 * deliberately NOT `money()`: a spreadsheet column wants `9500.00`, not
 * `$9,500.00`, which Excel imports as text.
 */
export function centsToDecimal(cents: number): string {
  const negative = cents < 0
  const abs = Math.abs(Math.trunc(cents))
  const whole = Math.floor(abs / 100)
  const part = abs % 100
  return (negative ? '-' : '') + whole + '.' + String(part).padStart(2, '0')
}

// A leading one of these makes a spreadsheet treat the cell as a formula, so a
// contact called `=cmd|...` becomes an instruction rather than a name. Prefixing
// an apostrophe is the standard defusing and spreadsheets do not display it.
// `-` is deliberately NOT in the list: a negative number is a legitimate value and
// far more common here than a title starting with a minus sign.
const FORMULA_LEAD = ['=', '+', '@', '\t', '\r']

/** One field, quoted and escaped per RFC 4180. */
export function csvField(value: string | number | null | undefined): string {
  let text = value === null || value === undefined ? '' : String(value)
  if (text && FORMULA_LEAD.includes(text[0])) text = "'" + text
  // Leading/trailing spaces are quoted too, or they are silently trimmed on import.
  const mustQuote = /[",\r\n]/.test(text) || text !== text.trim()
  return mustQuote ? '"' + text.replace(/"/g, '""') + '"' : text
}

/**
 * A whole document. CRLF line endings, per RFC 4180 and what Excel expects.
 *
 * `rows` is written out in the order given and is never filtered here — deciding
 * what goes in the export is the caller's job, and a helper that dropped a row it
 * thought was empty is exactly the bug this module is tested against.
 */
export function toCsv(headers: string[], rows: (string | number | null | undefined)[][]): string {
  return [headers, ...rows]
    .map((r) => r.map(csvField).join(','))
    .join('\r\n')
}

/** The shape the export needs, so it can be built from a board row or a list row. */
export type CsvOpportunity = {
  id: number
  title: string
  value_cents: number
  stage_id: number
  status: string
  contact_name: string | null
  business_name: string | null
  source: string | null
  updated_at: string
}

export const OPPORTUNITY_HEADERS = [
  'Id',
  'Opportunity name',
  'Stage',
  'Status',
  'Value',
  'Contact',
  'Business name',
  'Source',
  'Updated',
]

/**
 * The selected opportunities, in the order given.
 *
 * `stageName` is a lookup rather than a name on the row because two stages of the
 * measured pipeline are both called "Call Back" (DECISIONS.md) — the row carries
 * the stage id, and only the board knows which of the two it is.
 */
export function opportunitiesCsv(
  rows: CsvOpportunity[],
  stageName: (stageId: number) => string,
): string {
  return toCsv(
    OPPORTUNITY_HEADERS,
    rows.map((o) => [
      o.id,
      o.title,
      stageName(o.stage_id),
      o.status,
      centsToDecimal(o.value_cents),
      o.contact_name,
      o.business_name,
      o.source,
      o.updated_at,
    ]),
  )
}

/** `opportunities-2026-09-10.csv`, and never a name with a `/` in it. */
export function csvFilename(prefix: string, at: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const stamp = `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
  return `${prefix.replace(/[^a-z0-9-]+/gi, '-').toLowerCase()}-${stamp}.csv`
}
