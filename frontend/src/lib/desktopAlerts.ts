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
