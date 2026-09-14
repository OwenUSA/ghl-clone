/**
 * The call-recording player's behaviour, apart from React (2026-09-14).
 *
 * The row under a call used to be decoration: a play icon wired to nothing, a
 * `▁▃▅▂▇` glyph "waveform", a hard-coded "0:00 / 0:35" and "1x" — with a native
 * `<audio controls>` under it that was the only real control. This is the real one,
 * bound to a single audio element per call: play/pause, a seek bar, elapsed / total,
 * and a 1x → 1.5x → 2x speed toggle.
 *
 * Imports nothing at runtime, so `backend/tests/test_call_player.py` runs this exact
 * file under node against a fake audio element.
 *
 * Three rules the thread depends on:
 *
 *  - **Nothing loads until play is pressed.** The element is created with
 *    `preload="none"` and NO `src`; the src is assigned on the first press. A thread
 *    with 50 calls must not pull 50 recordings through browser → CRM → OWEN → Quo.
 *  - **A recording that will not load says so.** The stream answers 404 (Quo has no
 *    audio) or 502 (Quo unreachable); either way the element errors or `play()`
 *    rejects, and the state becomes `error` — the row is replaced by "Recording
 *    unavailable" rather than a player frozen at 0:00.
 *  - **One recording plays at a time.** Pressing play on a call pauses whichever
 *    other call was playing, as a voicemail list does.
 */

/** The part of HTMLAudioElement this uses. A fake implements it in the tests. */
export type AudioLike = {
  paused: boolean
  currentTime: number
  duration: number
  playbackRate: number
  defaultPlaybackRate: number
  preload: string
  src: string
  play(): Promise<void> | void
  pause(): void
  addEventListener(type: string, fn: () => void): void
  removeEventListener(type: string, fn: () => void): void
}

export type PlayerStatus = 'idle' | 'loading' | 'playing' | 'paused' | 'error'

export type PlayerState = {
  status: PlayerStatus
  /** Seconds. */
  elapsed: number
  /** Seconds; the event's own duration until the media reports a real one. 0 = unknown. */
  total: number
  rate: number
  /** 0..1, for the seek bar. */
  progress: number
}

export type CallPlayer = {
  getState(): PlayerState
  subscribe(fn: () => void): () => void
  toggle(): void
  /** Jump to a fraction (0..1) of the recording. */
  seek(fraction: number): void
  cycleSpeed(): void
  destroy(): void
}

export const SPEEDS = [1, 1.5, 2] as const

export const UNAVAILABLE = 'Recording unavailable'

/** 1 → 1.5 → 2 → 1. Anything else starts over at 1. */
export function nextSpeed(rate: number): number {
  const i = SPEEDS.indexOf(rate as (typeof SPEEDS)[number])
  return i === -1 ? SPEEDS[0] : SPEEDS[(i + 1) % SPEEDS.length]
}

/** "0:07", "3:04", "1:02:03". Unknown or negative is "0:00". */
export function formatClock(seconds: number | null | undefined): string {
  const s = typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0
    ? Math.floor(seconds) : 0
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/** A call gets a player only when there is something to play. */
export function hasRecording(e: { recording_url?: string | null }): boolean {
  return typeof e.recording_url === 'string' && e.recording_url.trim() !== ''
}

/** Speed label, "1x" / "1.5x" / "2x". */
export const speedLabel = (rate: number) => `${rate}x`

let playing: CallPlayer | null = null

export function createCallPlayer(
  audio: AudioLike, src: string, opts: { knownDuration?: number | null } = {},
): CallPlayer {
  const known = typeof opts.knownDuration === 'number' && opts.knownDuration > 0
    ? opts.knownDuration : 0
  let state: PlayerState = { status: 'idle', elapsed: 0, total: known, rate: 1, progress: 0 }
  let loaded = false
  let pendingStart: number | null = null
  const listeners = new Set<() => void>()

  audio.preload = 'none'

  const set = (patch: Partial<PlayerState>) => {
    const next = { ...state, ...patch }
    next.progress = next.total > 0 ? Math.min(1, Math.max(0, next.elapsed / next.total)) : 0
    if (next.status === state.status && next.elapsed === state.elapsed
        && next.total === state.total && next.rate === state.rate) return
    state = next   // a new object only on change: React's useSyncExternalStore needs that
    listeners.forEach((fn) => fn())
  }

  const fail = () => {
    if (playing === self) playing = null
    set({ status: 'error' })
  }

  const mediaDuration = () =>
    Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0

  const handlers: Record<string, () => void> = {
    loadedmetadata: () => {
      audio.playbackRate = state.rate
      if (pendingStart !== null) {
        audio.currentTime = pendingStart
        pendingStart = null
      }
      set({ total: mediaDuration() || state.total })
    },
    durationchange: () => set({ total: mediaDuration() || state.total }),
    timeupdate: () => {
      if (pendingStart === null) set({ elapsed: audio.currentTime })
    },
    playing: () => set({ status: 'playing' }),
    waiting: () => { if (state.status !== 'error') set({ status: 'loading' }) },
    pause: () => { if (state.status !== 'error') set({ status: 'paused' }) },
    ended: () => set({ status: 'paused', elapsed: mediaDuration() || state.total }),
    error: fail,
  }
  for (const [type, fn] of Object.entries(handlers)) audio.addEventListener(type, fn)

  const self: CallPlayer = {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn)
      return () => { listeners.delete(fn) }
    },
    toggle() {
      if (state.status === 'error') return
      if (state.status === 'playing' || state.status === 'loading') {
        audio.pause()
        set({ status: 'paused' })
        return
      }
      if (playing && playing !== self) pauseOther(playing)
      playing = self
      if (!loaded) {
        loaded = true
        audio.src = src            // the first network request for this recording
      }
      audio.defaultPlaybackRate = state.rate
      audio.playbackRate = state.rate
      set({ status: 'loading' })
      try {
        const p = audio.play()
        if (p && typeof p.then === 'function') {
          p.catch((err: { name?: string }) => {
            // A pause() racing the load rejects with AbortError: that is the user
            // pausing, not the recording failing.
            if (err && err.name === 'AbortError') return
            fail()
          })
        }
      } catch {
        fail()
      }
    },
    seek(fraction) {
      if (state.status === 'error' || !(state.total > 0)) return
      const f = Math.min(1, Math.max(0, Number(fraction) || 0))
      const at = f * state.total
      if (loaded && mediaDuration() > 0) {
        audio.currentTime = at
      } else {
        // Not loaded yet: remember it, apply once the media knows its length.
        pendingStart = at
      }
      set({ elapsed: at })
    },
    cycleSpeed() {
      const rate = nextSpeed(state.rate)
      audio.defaultPlaybackRate = rate
      audio.playbackRate = rate
      set({ rate })
    },
    destroy() {
      for (const [type, fn] of Object.entries(handlers)) audio.removeEventListener(type, fn)
      if (playing === self) playing = null
      if (!audio.paused) audio.pause()
      listeners.clear()
    },
  }
  return self
}

function pauseOther(other: CallPlayer) {
  const st = other.getState().status
  if (st === 'playing' || st === 'loading') other.toggle()
}
