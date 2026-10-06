/**
 * Desktop notifications for the bell (2026-09-30, the owner's Q5): while the CRM is open — even
 * in a background tab — a NEW alert also appears as the operating system's own notification.
 * Nothing leaves the building: it is the browser showing what the bell already has.
 *
 * `fresh` is pure (tests/test_dispatch_ui.py runs it under node): the first list a tab sees is
 * only remembered, so opening the CRM never replays old alerts; after that, an unread alert
 * not seen before is fresh.
 */
export type Seen = { primed: boolean; ids: Set<number> }

export function fresh<T extends { id: number; read: boolean }>(seen: Seen, items: T[]): T[] {
  const out = seen.primed ? items.filter((a) => !a.read && !seen.ids.has(a.id)) : []
  for (const a of items) seen.ids.add(a.id)
  seen.primed = true
  return out
}

export function supported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window
}

export function permission(): NotificationPermission | 'unsupported' {
  return supported() ? Notification.permission : 'unsupported'
}

export async function askPermission(): Promise<NotificationPermission | 'unsupported'> {
  if (!supported()) return 'unsupported'
  return Notification.requestPermission()
}

/** Show one. The tag is the alert's id, so two open tabs show it once. */
export function show(a: { id: number; title: string; body: string | null; urgent: boolean },
  onClick: () => void): void {
  if (permission() !== 'granted') return
  try {
    const n = new Notification(a.title, { body: a.body ?? undefined, tag: `ghl-alert-${a.id}`,
      requireInteraction: a.urgent })
    n.onclick = () => { window.focus(); onClick(); n.close() }
  } catch {
    // Some browsers only allow notifications from a service worker; the bell still has it.
  }
}

/**
 * A live AI call (2026-10-06, decision 19): "AI is on a call with <name>", ONCE per call. Same
 * opt-in as the bell — nothing shows unless this browser granted notifications ("Turn on desktop
 * alerts" in the bell). The tag is the call's linkedid, so two open tabs show it once; `announced`
 * remembers the calls this tab already showed, and the first list a tab sees is only remembered
 * (opening the CRM mid-call does not announce it), exactly like `fresh`.
 */
export function freshCalls<T extends { linkedid: string }>(seen: { primed: boolean; ids: Set<string> }, calls: T[]): T[] {
  const out = seen.primed ? calls.filter((c) => !seen.ids.has(c.linkedid)) : []
  for (const c of calls) seen.ids.add(c.linkedid)
  seen.primed = true
  return out
}

export function showCall(c: { linkedid: string; who: string; agent?: string | null }, onClick: () => void): void {
  if (permission() !== 'granted') return
  try {
    const n = new Notification(`AI is on a call with ${c.who}`, {
      body: c.agent ? `${c.agent} answered. Listen or take over from the CRM.` : 'Listen or take over from the CRM.',
      tag: `ghl-live-call-${c.linkedid}`,
    })
    n.onclick = () => { window.focus(); onClick(); n.close() }
  } catch {
    // Some browsers only allow notifications from a service worker; the banner still shows it.
  }
}
