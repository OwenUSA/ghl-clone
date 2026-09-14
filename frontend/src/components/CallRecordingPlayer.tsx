import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { IconPause, IconPlay } from './Icon'
import {
  UNAVAILABLE, createCallPlayer, formatClock, speedLabel,
  type CallPlayer, type PlayerState,
} from '../lib/callPlayer'

/**
 * One call's recording, GoHighLevel's compact row: a blue round play button, a seek
 * bar, "elapsed / total" and a speed toggle. The behaviour lives in lib/callPlayer.ts
 * (executed under node by test_call_player.py); this only draws it.
 *
 * Exactly ONE audio element, with no `src` and `preload="none"` until play is
 * pressed. Rendered only for a call that has a `recording_url` — the caller checks
 * `hasRecording` — so a call without audio shows no player at all.
 */
const BLUE = 'rgb(21,112,239)'
const TEXT = 'rgb(71,84,103)'

const noSubscribe = () => () => {}

export function CallRecordingPlayer({ src, knownDuration }: {
  src: string
  knownDuration?: number | null
}) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [player, setPlayer] = useState<CallPlayer | null>(null)
  const [initial] = useState<PlayerState>(() => ({
    status: 'idle', elapsed: 0, total: knownDuration && knownDuration > 0 ? knownDuration : 0,
    rate: 1, progress: 0,
  }))

  useEffect(() => {
    if (!audioRef.current) return
    const p = createCallPlayer(audioRef.current, src, { knownDuration })
    setPlayer(p)
    return () => p.destroy()
  }, [src, knownDuration])

  const state = useSyncExternalStore(
    player ? player.subscribe : noSubscribe,
    () => (player ? player.getState() : initial),
  )

  if (state.status === 'error') {
    return (
      <div role="status" style={{ marginTop: 8, fontSize: 12, color: TEXT }}>
        {UNAVAILABLE}
      </div>
    )
  }

  const active = state.status === 'playing' || state.status === 'loading'
  return (
    <div className="mt-2 flex items-center gap-2">
      <audio ref={audioRef} preload="none" />
      <button
        type="button"
        onClick={() => player?.toggle()}
        aria-label={active ? 'Pause recording' : 'Play recording'}
        aria-busy={state.status === 'loading'}
        className="flex shrink-0 items-center justify-center"
        style={{ width: 26, height: 26, padding: 0, border: 0, background: 'none',
                 cursor: 'pointer', borderRadius: '50%' }}
      >
        {active ? <IconPause size={26} color={BLUE} /> : <IconPlay size={26} color={BLUE} />}
      </button>
      <input
        type="range"
        min={0}
        max={1000}
        step={1}
        value={Math.round(state.progress * 1000)}
        onChange={(ev) => player?.seek(Number(ev.currentTarget.value) / 1000)}
        disabled={!(state.total > 0)}
        aria-label="Seek"
        aria-valuetext={`${formatClock(state.elapsed)} of ${formatClock(state.total)}`}
        style={{ width: 140, height: 4, accentColor: BLUE, cursor: 'pointer' }}
      />
      <span style={{ fontSize: 12, color: TEXT, fontVariantNumeric: 'tabular-nums',
                     whiteSpace: 'nowrap' }}>
        {formatClock(state.elapsed)} / {formatClock(state.total)}
      </span>
      <button
        type="button"
        onClick={() => player?.cycleSpeed()}
        aria-label={`Playback speed ${speedLabel(state.rate)}`}
        style={{ fontSize: 12, color: TEXT, border: 0, background: 'none', padding: '0 2px',
                 cursor: 'pointer', minWidth: 28, textAlign: 'left' }}
      >
        {speedLabel(state.rate)}
      </button>
    </div>
  )
}
