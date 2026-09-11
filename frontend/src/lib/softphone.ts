// The CRM browser softphone.
//
// A thin wrapper over SIP.js's SimpleUser facade, following the working implementation in
// the telephony project (owen-main `frontend/src/lib/softphone.ts`) rather than inventing
// a second approach. What this one adds is the thing the owner asked for and that one did
// not need: the registration state has to be VISIBLE and TRUE, because this browser is one
// leg of a ring group whose other legs are two mobile phones. A browser that has silently
// stopped being registered is worse than no browser at all -- the call rings the mobiles,
// somebody stares at a CRM that looks ready, and nobody knows the desk was dead.
//
// So every status here comes from a SIP.js delegate callback (onRegistered, onUnregistered,
// onServerConnect, onServerDisconnect) rather than from "we called register() and it did
// not throw". The one place the UI can say "ready" is when Asterisk has confirmed a
// registration.
//
// What this client does NOT do, deliberately: it drives its OWN leg only -- answer, hang
// up, mute. Bridging, hold and transfer are backend/ARI concerns in the telephony project
// and the browser never talks to ARI. In particular, **answering here stops the mobiles
// ringing all by itself**: `hybrid_ring_and_bridge` in owen-main hangs up every other leg
// before it bridges the winner (its own test, `test_first_to_answer_is_bridged_and_the_
// rest_are_torn_down`, asserts the losers are dropped BEFORE the bridge is created). There
// is nothing to implement at this end, and implementing something would be a second,
// competing answer to a question already settled.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Web } from 'sip.js'
import { ApiError } from './api'
import { SoftphoneLease } from './softphoneLease'
import { fetchSoftphoneCredentials, type SoftphoneCredentials } from './softphoneApi'

export type SoftphoneStatus =
  /** This deployment has no phone system configured. The dock hides itself. */
  | 'unavailable'
  /** Configured, but this user is not a provisioned operator. */
  | 'no-operator'
  /** Switched off by the user. Calls ring the mobiles only. */
  | 'off'
  | 'connecting'
  /** Registered with Asterisk. This browser will ring. */
  | 'ready'
  /** The socket dropped; retrying. NOT ringing while this says so. */
  | 'reconnecting'
  /** Another tab is the phone (an operator AOR holds one contact). */
  | 'elsewhere'
  /** Could not register, and is not retrying. `error` says why. */
  | 'failed'

export type CallPhase = 'idle' | 'ringing' | 'in-call'

export type SoftphoneState = {
  status: SoftphoneStatus
  phase: CallPhase
  /** Why the phone is not ready, or why the last call failed. Shown verbatim. */
  error: string | null
  /** The OWEN operator endpoint this browser registered as. */
  operator: string | null
  /** The calling party's number, from the INVITE's From header. */
  peer: string | null
  /** The DID that was dialled, from the INVITE's display name. */
  dialed: string | null
  answeredAt: number | null
  muted: boolean
}

const IDLE: Pick<SoftphoneState, 'phase' | 'peer' | 'dialed' | 'answeredAt' | 'muted'> = {
  phase: 'idle',
  peer: null,
  dialed: null,
  answeredAt: null,
  muted: false,
}

/** Remember the user's own choice across reloads. Per browser, never sent anywhere. */
const PREFERENCE = 'ghl.softphone.online'

function wantedOnline(): boolean {
  try {
    // Default ON. A phone somebody has to switch on every morning is a phone that is off
    // when the call comes in; switching it off is the deliberate act, not switching it on.
    return window.localStorage.getItem(PREFERENCE) !== 'off'
  } catch {
    return true
  }
}

function rememberOnline(on: boolean): void {
  try {
    window.localStorage.setItem(PREFERENCE, on ? 'on' : 'off')
  } catch {
    /* private mode: the preference simply does not persist */
  }
}

/** The <audio> element remote call audio is routed into. Created once, lazily. */
function remoteAudio(): HTMLAudioElement {
  let el = document.getElementById('ghl-softphone-audio') as HTMLAudioElement | null
  if (!el) {
    el = document.createElement('audio')
    el.id = 'ghl-softphone-audio'
    el.autoplay = true
    document.body.appendChild(el)
  }
  return el
}

/**
 * Ask for the microphone BEFORE registering, not when the phone is already ringing.
 *
 * A blocked microphone does not stop a registration, so without this the failure surfaces
 * at the worst possible moment: the card appears, the user hits Answer, and SIP.js turns
 * the getUserMedia rejection into a 480 that reads like a dropped call. Asking up front
 * turns "the call failed" into "your microphone is blocked", days earlier.
 *
 * The tracks are stopped immediately; this is a permission probe, not a capture.
 */
async function microphoneIsUsable(): Promise<string | null> {
  if (!navigator.mediaDevices?.getUserMedia) {
    return 'This browser cannot use a microphone, so it cannot answer calls.'
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.getTracks().forEach((t) => t.stop())
    return null
  } catch (e) {
    const name = (e as { name?: string })?.name || ''
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return 'Microphone blocked. Allow the microphone for this site, then switch the phone on again.'
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      return 'No microphone found, so this browser cannot answer calls.'
    }
    return `The microphone could not be opened (${name || 'unknown error'}).`
  }
}

/** Backoff for a dropped socket: 2s, 4s, 8s, 16s, then every 30s. Never gives up -- a */
/* laptop that closed its lid must come back as a phone without anybody clicking. */
function retryDelay(attempt: number): number {
  return Math.min(30_000, 2_000 * 2 ** Math.min(attempt, 4))
}

/** Re-mint this long before the credentials lapse, so a registration never expires mid-shift. */
const REMINT_MARGIN_MS = 120_000

export function useSoftphone() {
  const [state, setState] = useState<SoftphoneState>({
    status: 'off',
    error: null,
    operator: null,
    ...IDLE,
  })

  const userRef = useRef<Web.SimpleUser | null>(null)
  const leaseRef = useRef<SoftphoneLease | null>(null)
  const expiresAtRef = useRef<number>(0)
  const retryRef = useRef<{ timer: ReturnType<typeof setTimeout> | null; attempt: number }>({
    timer: null,
    attempt: 0,
  })
  // The delegate callbacks close over the render that created them, where `phase` is still
  // whatever it was at connect time. Refs are always current, so a callback reads the truth.
  const wantOnlineRef = useRef(false)
  const phaseRef = useRef<CallPhase>('idle')
  const connectingRef = useRef(false)
  // Bumped by every teardown. A connect that started before one captures the generation
  // it began in and abandons itself if that changed while it was awaiting. Without it,
  // React 19's StrictMode double-mount (and any fast off/on) leaves a half-built SIP user
  // registering behind the one the app thinks it has -- two registrations racing for an
  // AOR that holds exactly one contact.
  const genRef = useRef(0)

  const patch = useCallback((p: Partial<SoftphoneState>) => {
    setState((s) => {
      if (p.phase !== undefined) phaseRef.current = p.phase
      return { ...s, ...p }
    })
  }, [])

  const clearRetry = useCallback(() => {
    if (retryRef.current.timer !== null) clearTimeout(retryRef.current.timer)
    retryRef.current = { timer: null, attempt: 0 }
  }, [])

  /** Drop the SIP user without changing what the user WANTS. Used by teardown and retry. */
  const teardown = useCallback(async () => {
    genRef.current += 1
    const user = userRef.current
    userRef.current = null
    expiresAtRef.current = 0
    if (!user) return
    try {
      await user.unregister()
    } catch {
      /* never registered, or the socket is already gone */
    }
    try {
      await user.disconnect()
    } catch {
      /* already disconnected */
    }
  }, [])

  const connect = useCallback(async () => {
    if (connectingRef.current || userRef.current) return
    connectingRef.current = true
    const generation = genRef.current
    const stale = () => genRef.current !== generation
    // Set when we abandon a half-built connection, so the `finally` can start the one the
    // app actually asked for rather than leaving the phone silently off.
    let abandoned = false
    patch({ status: 'connecting', error: null })
    try {
      const micProblem = await microphoneIsUsable()
      if (stale()) {
        abandoned = true
        return
      }
      if (micProblem) {
        // Registering anyway would put "ready" on screen for a browser that cannot answer.
        patch({ status: 'failed', error: micProblem })
        return
      }

      let creds: SoftphoneCredentials
      try {
        creds = await fetchSoftphoneCredentials()
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0
        const message = (e as Error)?.message || 'the softphone could not start'
        if (status === 503) {
          // No phone system on this deployment: not an error the user can act on.
          patch({ status: 'unavailable', error: null })
        } else if (status === 403) {
          patch({ status: 'no-operator', error: message })
        } else {
          // A 502 is the phone system being unreachable -- worth retrying on its own.
          patch({ status: 'failed', error: message })
          scheduleRetry()
        }
        return
      }

      if (stale()) {
        abandoned = true
        return
      }

      expiresAtRef.current = creds.sip.expires_at * 1000
      const user = new Web.SimpleUser(creds.sip.wss_url, {
        aor: `sip:${creds.sip.username}@${creds.sip.domain}`,
        media: { constraints: { audio: true, video: false }, remote: { audio: remoteAudio() } },
        userAgentOptions: {
          authorizationUsername: creds.sip.authorization_username,
          authorizationPassword: creds.sip.password,
          transportOptions: { server: creds.sip.wss_url },
          sessionDescriptionHandlerFactoryOptions: {
            iceServers: creds.ice_servers.map((s) => ({
              urls: s.urls,
              username: s.username,
              credential: s.credential,
            })),
          },
        },
        delegate: {
          // Asterisk confirmed the registration. This is the ONLY thing that puts
          // "ready" on screen.
          onRegistered: () => {
            clearRetry()
            patch({ status: 'ready', error: null, operator: creds.operator })
          },
          onUnregistered: () => {
            if (!wantOnlineRef.current) return
            // We did not ask for this: the registration expired, or Asterisk dropped it
            // (another contact took the AOR). Say so, and get it back.
            patch({
              status: 'reconnecting',
              error: 'The registration was dropped — reconnecting.',
            })
            scheduleRetry()
          },
          onServerDisconnect: (error?: Error) => {
            if (!wantOnlineRef.current) return
            patch({
              status: 'reconnecting',
              error: error?.message
                ? `Connection lost (${error.message}) — reconnecting.`
                : 'Connection lost — reconnecting.',
            })
            scheduleRetry()
          },
          onCallReceived: () => {
            // The pending INVITE carries both identities: the From URI user is the
            // caller's number, and the display name is the DID they dialled -- Asterisk
            // stamps the operator leg's caller-ID that way (owen-main `ring.operator_
            // caller_id`). SimpleUser hides the session, so reach it loosely.
            const session = (userRef.current as unknown as { session?: RemoteIdentity })
              ?.session
            const identity = session?.remoteIdentity
            patch({
              phase: 'ringing',
              peer: identity?.uri?.user || null,
              dialed: identity?.displayName || null,
              answeredAt: null,
              muted: false,
              error: null,
            })
          },
          onCallAnswered: () => {
            patch({ phase: 'in-call', answeredAt: Date.now(), muted: false })
            watchMedia()
          },
          onCallHangup: () => {
            patch({ ...IDLE })
          },
        },
      })
      userRef.current = user
      await user.connect()
      await user.register()
      if (stale()) {
        // Something tore us down mid-handshake. Drop this registration rather than
        // leaving it holding the AOR contact the next one is about to want.
        abandoned = true
        await teardown()
        return
      }
      // Status stays 'connecting' until onRegistered fires. register() resolving only
      // means the REGISTER was sent.
    } catch (e) {
      await teardown()
      patch({ status: 'failed', error: (e as Error)?.message || 'the softphone could not start' })
      if (wantOnlineRef.current) scheduleRetry()
    } finally {
      connectingRef.current = false
      // Re-arm. A connect abandoned above was replaced by an intent that arrived while it
      // was in flight; that intent found `connectingRef` set and did nothing, so it is
      // this call's job to hand the phone back.
      if (abandoned && wantOnlineRef.current && leaseRef.current?.isHeld) {
        setTimeout(() => {
          if (wantOnlineRef.current) void connect()
        }, 0)
      }
    }
    // scheduleRetry/watchMedia are stable callbacks defined below; they are intentionally
    // not dependencies (they would recreate `connect` on every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearRetry, patch, teardown])

  /**
   * Come back after a dropped socket. Tears the old SIP user down first: reconnecting on
   * top of a half-dead transport leaves two registrations racing for one AOR contact.
   */
  const scheduleRetry = useCallback(() => {
    if (!wantOnlineRef.current || retryRef.current.timer !== null) return
    const attempt = retryRef.current.attempt
    retryRef.current.timer = setTimeout(() => {
      retryRef.current = { timer: null, attempt: attempt + 1 }
      if (!wantOnlineRef.current) return
      void teardown().then(() => {
        if (wantOnlineRef.current) void connect()
      })
    }, retryDelay(attempt))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teardown, connect])

  /**
   * Watch the live call's ICE state.
   *
   * This is how a TURN problem actually presents: registration is fine (that is plain
   * WebSocket signalling), the call connects, and then there is silence because no media
   * path could be negotiated through the firewall. Without this the user is left holding a
   * call that looks connected and hears nothing.
   */
  const watchMedia = useCallback(() => {
    const session = (userRef.current as unknown as { session?: SdhSession })?.session
    const pc = session?.sessionDescriptionHandler?.peerConnection
    if (!pc) return
    pc.oniceconnectionstatechange = () => {
      if (pc.iceConnectionState === 'failed') {
        patch({
          error:
            'No audio path could be established (the TURN relay is unreachable from this ' +
            'network). The call was dropped — try a mobile.',
        })
        void hangup()
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch])

  // --- the controls -------------------------------------------------------------

  const answer = useCallback(async () => {
    const user = userRef.current
    if (!user) return
    try {
      await user.answer()
    } catch (e) {
      // Almost always the microphone. SIP.js turns a session-description failure into a
      // 480, so the INVITE is REJECTED rather than answered -- the ring group carries on
      // ringing the mobiles, which is the right outcome. Say what happened, plainly.
      patch({
        ...IDLE,
        error:
          'Could not answer — ' +
          ((e as Error)?.message || 'the microphone could not be opened') +
          '. The call is still ringing the other phones.',
      })
    }
  }, [patch])

  const decline = useCallback(async () => {
    const user = userRef.current
    try {
      await user?.decline()
    } catch {
      /* the caller hung up first */
    }
    // Declining here only opts THIS browser out. The mobiles keep ringing until the ring
    // group's timeout, exactly as they would if nobody were at the desk.
    patch({ ...IDLE })
  }, [patch])

  const hangup = useCallback(async () => {
    const user = userRef.current
    try {
      await user?.hangup()
    } catch {
      // hangup() rejects when the session is already tearing down, which is common for a
      // server-bridged leg. The user's intent is to leave, so always return to idle --
      // letting that reject escape once left an in-call panel on screen forever.
    } finally {
      patch({ ...IDLE })
    }
  }, [patch])

  const toggleMute = useCallback(() => {
    const user = userRef.current
    if (!user) return
    setState((s) => {
      try {
        if (s.muted) user.unmute()
        else user.mute()
      } catch {
        return s // no live session; leave the flag alone
      }
      return { ...s, muted: !s.muted }
    })
  }, [])

  /** Switch the phone on or off. The choice is remembered for this browser. */
  const setOnline = useCallback(
    (on: boolean) => {
      wantOnlineRef.current = on
      rememberOnline(on)
      clearRetry()
      if (on) {
        leaseRef.current?.claim()
      } else {
        leaseRef.current?.release()
        void teardown().then(() => patch({ status: 'off', error: null, ...IDLE }))
      }
    },
    [clearRetry, patch, teardown],
  )

  // --- lifecycle ----------------------------------------------------------------

  useEffect(() => {
    const lease = new SoftphoneLease((event) => {
      if (event === 'granted') {
        if (wantOnlineRef.current) void connect()
      } else {
        // Another tab took the phone. Stand down before Asterisk evicts us, so the two
        // tabs never both believe they are registered.
        clearRetry()
        void teardown().then(() =>
          patch({
            status: 'elsewhere',
            error: null,
            ...IDLE,
          }),
        )
      }
    })
    leaseRef.current = lease

    wantOnlineRef.current = wantedOnline()
    if (wantOnlineRef.current) lease.claim()
    else patch({ status: 'off' })

    return () => {
      wantOnlineRef.current = false
      clearRetry()
      lease.close()
      leaseRef.current = null
      void teardown()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Re-mint before the credentials lapse, and only while idle: re-registering mid-call
  // would drop the call to renew a credential that the call does not need.
  useEffect(() => {
    const timer = setInterval(() => {
      if (!wantOnlineRef.current || phaseRef.current !== 'idle') return
      if (state.status !== 'ready') return
      if (!expiresAtRef.current) return
      if (Date.now() < expiresAtRef.current - REMINT_MARGIN_MS) return
      void teardown().then(() => {
        if (wantOnlineRef.current) void connect()
      })
    }, 30_000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.status])

  // Closing the tab mid-call: warn first, then do the best we can on the way out.
  //
  // The warning is the real protection -- a BYE sent from `unload` is best-effort and the
  // socket may die before it leaves. Without the prompt, closing a tab drops a live
  // customer call into silence until Asterisk notices the transport is gone.
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (phaseRef.current === 'in-call') {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    const leave = () => {
      // Synchronous best effort. If the BYE does not make it out, Asterisk tears the call
      // down when the WebSocket closes a moment later.
      try {
        void userRef.current?.hangup()
      } catch {
        /* nothing to hang up */
      }
      try {
        void userRef.current?.unregister()
      } catch {
        /* nothing registered */
      }
    }
    window.addEventListener('beforeunload', warn)
    window.addEventListener('pagehide', leave)
    return () => {
      window.removeEventListener('beforeunload', warn)
      window.removeEventListener('pagehide', leave)
    }
  }, [])

  return { state, setOnline, answer, decline, hangup, toggleMute, takeOver: () => setOnline(true) }
}

export type SoftphoneApi = ReturnType<typeof useSoftphone>

// SIP.js's SimpleUser deliberately hides its session; these are the two shapes we reach
// through it for, named rather than cast to `any` at each site.
type RemoteIdentity = {
  remoteIdentity?: { uri?: { user?: string }; displayName?: string }
}
type SdhSession = {
  sessionDescriptionHandler?: { peerConnection?: RTCPeerConnection }
}
