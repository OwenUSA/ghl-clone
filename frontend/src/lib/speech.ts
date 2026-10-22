/**
 * Pure helpers for the mic button (components/MicButton.tsx). No React, no imports.
 */

/** What was typed, then what was said: one space between, nothing added to an empty box. */
export function joinSpoken(typed: string, spoken: string): string {
  const s = spoken.trim()
  if (!s) return typed
  if (!typed.trim()) return s
  return /\s$/.test(typed) ? typed + s : `${typed} ${s}`
}

/** "0:07" for the recording timer. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** The recorder types to ask for, best first; Safari / iPhone only records MP4. */
export const RECORDER_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

/** The most a person can speak in one go; the recording stops by itself here. */
export const MAX_SECONDS = 120
