/**
 * The ONE way a screen places a call (2026-09-14).
 *
 * Every call button — the Conversations dialer, a thread's phone icon, an opportunity
 * card's call icon — goes through `useCallLauncher().launch`. It does the three things
 * that must never be left to a call site:
 *
 *  1. decides whether the call rings THIS browser: yes when its phone is Ready and idle,
 *     otherwise owen-main rings its default operator exactly as before;
 *  2. marks the outbound intent with the number BEFORE the request leaves, so the INVITE
 *     that comes back is answered here instead of showing "Incoming call" (owen-main's
 *     lesson: an intent hung off each call site was forgotten by one of them);
 *  3. clears that intent the moment the server refuses or the request fails, so nothing
 *     can be claimed for a call that is not coming.
 *
 * It also remembers WHO was called (the name and ids the screen already had), so the
 * in-call window can title the call and offer the right action afterwards without a
 * second lookup.
 */
import { useCallback } from 'react'
import { clearOutboundIntent, markOutboundIntent } from './outboundIntent'
import { useSoftphoneContext } from './softphoneContext'
import type { CallPlaced } from './api'

export type CallTarget = {
  /** The number being rung. Null when the screen does not know it (then it cannot ring
   *  this browser, because the intent could not be matched to the INVITE). */
  number: string | null
  contactId?: number | null
  contactName?: string | null
  conversationId?: number | null
}

type Remembered = CallTarget & { digits: string }

let remembered: Remembered | null = null

const lastTen = (n: string | null | undefined) => {
  const d = String(n ?? '').replace(/\D/g, '')
  return d.length >= 10 ? d.slice(-10) : ''
}

/** Who a live or just-ended call is with, if this tab placed it. Matched by number. */
export function calledTarget(peer: string | null | undefined): CallTarget | null {
  const d = lastTen(peer)
  return remembered && d && remembered.digits === d ? remembered : null
}

export type Launched<T extends CallPlaced> = T & {
  /** True when this browser is the phone that rings first. */
  viaBrowser: boolean
}

export function useCallLauncher() {
  const { state } = useSoftphoneContext()
  const ready = state.status === 'ready' && state.phase === 'idle'

  const launch = useCallback(
    async <T extends CallPlaced>(
      target: CallTarget,
      request: (ringBrowser: boolean) => Promise<T & { number?: string | null;
        contact_id?: number | null; contact_name?: string | null;
        conversation_id?: number | null }>,
    ): Promise<Launched<T>> => {
      const viaBrowser = ready && lastTen(target.number) !== ''
      if (viaBrowser) markOutboundIntent(target.number as string)
      let r
      try {
        r = await request(viaBrowser)
      } catch (e) {
        if (viaBrowser) clearOutboundIntent()
        throw e
      }
      if (!r.placed) {
        if (viaBrowser) clearOutboundIntent()
      } else {
        const number = r.number ?? target.number
        remembered = {
          number,
          digits: lastTen(number),
          contactId: r.contact_id !== undefined ? r.contact_id : target.contactId ?? null,
          contactName: r.contact_name !== undefined ? r.contact_name : target.contactName ?? null,
          conversationId: r.conversation_id ?? target.conversationId ?? null,
        }
      }
      return { ...r, viaBrowser }
    },
    [ready],
  )

  return { launch, ready, status: state.status, phase: state.phase, error: state.error }
}
