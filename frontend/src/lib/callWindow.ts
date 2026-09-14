/**
 * The words and decisions of the dialer and the in-call window, apart from React.
 *
 * Imports nothing, so node executes it in `backend/tests/test_dialer_ui.py`.
 */

/**
 * Why this browser cannot place a call right now, or null when it can.
 *
 * Calling needs the browser phone Ready — the green state of the status dot — because
 * owen-main rings THIS browser first and then the customer. A browser that is not
 * registered would leave the customer's phone never ringing while the dialer said
 * "Calling…". Each sentence says what to do about it.
 *
 * `status` and `phase` are `SoftphoneStatus` / `CallPhase` from `lib/softphone.ts`,
 * passed as strings so this file needs no import.
 */
export function phoneNotReadyReason(status: string, phase: string = 'idle'): string | null {
  if (status === 'ready' && phase !== 'idle') {
    return 'You are already on a call — hang up first.'
  }
  switch (status) {
    case 'ready':
      return null
    case 'off':
      return 'Your browser phone is switched off — switch it on to call.'
    case 'connecting':
      return 'Your browser phone is still connecting — try again in a moment.'
    case 'reconnecting':
      return 'Your browser phone lost its connection and is reconnecting — you can call once it is back.'
    case 'elsewhere':
      return 'Another tab is your browser phone — call from that tab, or make this tab the phone from the status dot.'
    case 'failed':
      return 'Your browser phone could not connect — the status dot, top right, says why.'
    case 'no-operator':
      return 'You are not set up to make calls from the browser — ask for a browser phone to be added for you.'
    case 'unavailable':
      return 'Browser calling is not set up on this CRM.'
    default:
      return 'Your browser phone is not ready.'
  }
}

/** `0:07`, `12:03`, `1:02:03`. The in-call timer and the after-call duration. */
export function formatCallDuration(ms: number): string {
  const total = Math.max(0, Math.floor((Number.isFinite(ms) ? ms : 0) / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

export type AfterCall = {
  /** The number is a contact: open their conversation. */
  openConversation: boolean
  /** The number is nobody: offer the add-contact form, prefilled. Never saves by itself. */
  addContact: boolean
}

/** What the window offers once a call has ended. Exactly one of the two, never both. */
export function afterCallActions(contactId: number | null | undefined): AfterCall {
  const known = contactId !== null && contactId !== undefined
  return { openConversation: known, addContact: !known }
}

/** The line along the top of the window: the contact's name, else the number. */
export function callTitle(contactName: string | null | undefined, formattedNumber: string): string {
  const name = String(contactName ?? '').trim()
  return name || formattedNumber || 'Unknown number'
}
