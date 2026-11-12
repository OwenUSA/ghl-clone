/**
 * The after-inspection report editor's pure helpers (2026-11-12). IMPORT-FREE, like owenAi.ts,
 * so they can run under node in a backend test. The server validates every save; these only
 * shape what the page edits.
 */

export const TIERS = ['GOOD', 'BETTER', 'BEST'] as const
export type Tier = (typeof TIERS)[number]
export const TIER_COLOR: Record<Tier, string> = {
  GOOD: 'rgb(96,116,150)', BETTER: 'rgb(196,146,18)', BEST: 'rgb(122,62,168)',
}

export type RPhoto = { path: string; num?: number; marks?: unknown[]; brighten?: boolean; crop_gps?: boolean }
export type RArea = { points: number[][]; kind: 'covered' | 'excluded'; label?: string }
export type RCoverage = { photo: RPhoto; areas: RArea[]; caption?: string }
export type RStep = { title: string; text: string }
export type RPicture = {
  steps: RStep[]; callouts: string[]; section_title: string; layers: string[]
  top_title?: string; top_label: string; roof?: string; notes?: string
}
export type ROption = {
  name: Tier; headline: string; picture: RPicture; picture_upload?: string | null
  scope: string[]; days?: string; warranty?: string; coverage?: RCoverage[]
}
export type RCompare = { label: string; GOOD?: string; BETTER?: string; BEST?: string }
export type RGallery = { title: string; photos: { photo: RPhoto; caption?: string }[] }
export type RFinding = { title: string; saw: string; means: string; fix: string; [k: string]: unknown }
export type RSpec = {
  what_happened: string; weather: string; inspected_how: string; next_steps?: string
  badge: { severity: string; text?: string }; findings: RFinding[]
  galleries?: RGallery[] | null; gbb?: { intro?: string; options: ROption[]; compare?: RCompare[] } | null
  [k: string]: unknown
}

/** A deep copy that is safe to mutate (the spec is plain JSON). */
export function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T
}

/** The photo number a file name carries: 'lib/021-3fa9c1d2.jpg' -> 21, 'lib/104a-…' -> 104. */
export function photoNumber(path: string): number | null {
  const m = /(?:^|\/)(\d{1,4})(?!\d)[^/]*$/.exec(path)
  return m ? Number(m[1]) : null
}

/** A new area: a square in the middle of the picture. */
export function newArea(kind: RArea['kind'] = 'covered'): RArea {
  return { points: [[0.35, 0.35], [0.65, 0.35], [0.65, 0.65], [0.35, 0.65]], kind, label: '' }
}

const clamp = (v: number) => Math.min(1, Math.max(0, Math.round(v * 1000) / 1000))

/** Move one corner; values stay between 0 and 1 (3 decimals). */
export function movePoint(a: RArea, i: number, x: number, y: number): RArea {
  return { ...a, points: a.points.map((p, k) => (k === i ? [clamp(x), clamp(y)] : p)) }
}

/** A corner halfway to the next one, after corner i (at most 16). */
export function addPoint(a: RArea, i: number): RArea {
  if (a.points.length >= 16) return a
  const p = a.points[i]
  const q = a.points[(i + 1) % a.points.length]
  const mid = [clamp((p[0] + q[0]) / 2), clamp((p[1] + q[1]) / 2)]
  return { ...a, points: [...a.points.slice(0, i + 1), mid, ...a.points.slice(i + 1)] }
}

/** Remove corner i; an area keeps at least 3. */
export function removePoint(a: RArea, i: number): RArea {
  return a.points.length <= 3 ? a : { ...a, points: a.points.filter((_, k) => k !== i) }
}

/** Lines of a textarea <-> a list, blank lines dropped. */
export function linesToList(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean)
}

/** "$" figures are refused by the server (no prices until AHS decides); say it before saving. */
export function hasPrice(text: string): boolean {
  return /\$\s?\d|\b\d[\d,]*(?:\.\d\d)?\s?(?:dollars|usd)\b/i.test(text)
}

export const SOURCE_LABEL: Record<string, string> = {
  ai: 'Owen AI', page: 'Edited on the page', restore: 'Restored',
}

export const PICTURE_STATE_LABEL: Record<string, string> = {
  current: 'Up to date', stale: 'Out of date — its text changed', uploaded: 'Your own picture',
  missing: 'No picture yet',
}
