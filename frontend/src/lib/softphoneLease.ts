// Only ONE tab may be the phone.
//
// This is not tidiness, it is the shape of the endpoint. `asterisk/pjsip.conf` gives
// each operator an AOR with `max_contacts = 1` and `remove_existing = yes`, so a second
// browser tab registering as the same operator silently EVICTS the first. The evicted
// tab is never told: it holds an open WebSocket, its UI still says "ready", and it never
// rings again. That is precisely the failure this feature exists to prevent -- a
// softphone that has quietly stopped being a phone is worse than no softphone at all.
//
// So the tabs elect one holder between themselves, over a BroadcastChannel, and every
// other tab says so on screen with a "Use this tab instead" button. The rule is simply
// LAST CLAIM WINS, which matches what Asterisk is going to do anyway -- this only makes
// the eviction visible and deliberate rather than silent.
//
// Where BroadcastChannel is unavailable the lease degrades to "this tab always holds
// it": one tab is the normal case, and refusing to be a phone because the browser is old
// would be a worse answer than the eviction it is guarding against.

const CHANNEL = 'ghl.softphone.lease'

type Message =
  | { type: 'claim'; id: string; at: number }
  | { type: 'release'; id: string }

export type LeaseEvent = 'granted' | 'revoked'

/** Random per-tab identity. Never persisted: a reloaded tab is a new tab. */
const TAB_ID = Math.random().toString(36).slice(2) + Date.now().toString(36)

export class SoftphoneLease {
  private channel: BroadcastChannel | null = null
  private held = false
  private wanted = false
  private claimTimer: ReturnType<typeof setTimeout> | null = null

  private readonly onEvent: (e: LeaseEvent) => void

  constructor(onEvent: (e: LeaseEvent) => void) {
    // Assigned explicitly rather than as a constructor parameter property: this project
    // builds with `erasableSyntaxOnly`, which rejects the shorthand.
    this.onEvent = onEvent
    try {
      this.channel = new BroadcastChannel(CHANNEL)
      this.channel.onmessage = (e: MessageEvent<Message>) => this.receive(e.data)
    } catch {
      // No BroadcastChannel: single-tab mode, this tab always holds the lease.
      this.channel = null
    }
  }

  get isHeld(): boolean {
    return this.held
  }

  /** Ask to be the phone. Evicts whichever tab currently holds it, deliberately. */
  claim(): void {
    this.wanted = true
    this.cancelPendingClaim()
    this.post({ type: 'claim', id: TAB_ID, at: Date.now() })
    if (!this.held) {
      this.held = true
      this.onEvent('granted')
    }
  }

  /** Stop being the phone, and let a waiting tab take over. */
  release(): void {
    this.wanted = false
    this.cancelPendingClaim()
    if (this.held) {
      this.held = false
      this.post({ type: 'release', id: TAB_ID })
    }
  }

  close(): void {
    this.release()
    try {
      this.channel?.close()
    } catch {
      /* already closed */
    }
    this.channel = null
  }

  private receive(msg: Message): void {
    if (!msg || msg.id === TAB_ID) return
    if (msg.type === 'claim') {
      // Somebody else took it. Stand down BEFORE they register, so we are not the tab
      // holding a registration Asterisk is about to throw away.
      this.cancelPendingClaim()
      if (this.held) {
        this.held = false
        this.onEvent('revoked')
      }
      return
    }
    // A holder went away. If we still want the phone, take it -- after a short random
    // pause, so several waiting tabs do not claim in the same millisecond and evict each
    // other in turn. A claim arriving while we wait cancels ours.
    if (msg.type === 'release' && this.wanted && !this.held) {
      this.claimTimer = setTimeout(() => {
        this.claimTimer = null
        if (this.wanted && !this.held) this.claim()
      }, 50 + Math.floor(Math.random() * 250))
    }
  }

  private cancelPendingClaim(): void {
    if (this.claimTimer !== null) {
      clearTimeout(this.claimTimer)
      this.claimTimer = null
    }
  }

  private post(msg: Message): void {
    try {
      this.channel?.postMessage(msg)
    } catch {
      /* the channel closed under us; the lease degrades to single-tab */
    }
  }
}
