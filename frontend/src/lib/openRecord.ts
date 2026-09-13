/**
 * Open one record on another page — the palette's path (`openRecord` in App.tsx),
 * for a control that is not the palette.
 *
 * There is no router (DECISIONS.md), the board card is several components deep in
 * a page that other work owns, and the shell (App.tsx) is not this branch's to
 * edit. So the shell has to OFFER the capability: it registers its `openRecord`
 * here, and a control that needs it asks `canOpenRecords()` first.
 *
 * Until the shell registers, `canOpenRecords()` is false and every control that
 * depends on it — the card's "View conversations", the modal's links to the
 * contact and the conversation — is NOT RENDERED, rather than drawn as a button
 * that does nothing. The one-line App.tsx change is recorded in
 * .qa/state/oppmodal-blocked.
 */
export type OpenRecord = (view: string, id: number) => void

let handler: OpenRecord | null = null

/** Called by the shell once, with the palette's own `openRecord`. Returns an
    unregister function for a useEffect cleanup. */
export function registerOpenRecord(fn: OpenRecord): () => void {
  handler = fn
  return () => { if (handler === fn) handler = null }
}

export function canOpenRecords(): boolean {
  return handler !== null
}

export function openRecord(view: string, id: number): void {
  handler?.(view, id)
}
