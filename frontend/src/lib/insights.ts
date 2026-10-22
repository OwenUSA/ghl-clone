/**
 * Pure helpers for the Insights tab (app/insights). No React, no imports.
 */

/** "+3", "-2", "0", or "" when there is nothing to compare with. */
export function change(value: number, previous: number | null): string {
  if (previous === null || previous === undefined) return ''
  const d = Math.round((value - previous) * 100) / 100
  return d > 0 ? `+${d}` : `${d}`
}

/** 'good' | 'bad' | 'same' for a tile's change; a rise in callbacks is bad. */
export function tone(value: number, previous: number | null, lowerIsBetter: boolean): 'good' | 'bad' | 'same' {
  if (previous === null || previous === undefined || value === previous) return 'same'
  return (value > previous) !== lowerIsBetter ? 'good' : 'bad'
}

/** $1,200 for money tiles (label ends in "($)"), 1,200 otherwise. */
export function tileValue(label: string, value: number): string {
  const n = value.toLocaleString('en-US', { maximumFractionDigits: 2 })
  return label.endsWith('($)') ? `$${n}` : n
}

/** A tile label without its "($)". */
export function tileLabel(label: string): string {
  return label.replace(/\s*\(\$\)$/, '')
}

/** "technician" -> "Technician", "days_later" -> "Days later". */
export function columnLabel(key: string): string {
  const s = key.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** The first column (after the first) whose cells are all numbers: what a chart draws. */
export function numericColumn(rows: (string | number | null)[][]): number {
  if (!rows.length) return -1
  for (let i = 1; i < rows[0].length; i++) {
    if (rows.every((r) => typeof r[i] === 'number' || r[i] === null)) return i
  }
  return -1
}
