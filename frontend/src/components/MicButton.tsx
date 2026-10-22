import { useEffect, useRef, useState } from 'react'
import { transcribeSpeech } from '../lib/api'
import { MAX_SECONDS, RECORDER_TYPES, clock, joinSpoken } from '../lib/speech'

/**
 * Speak instead of type (2026-10-22): press, talk, press again. The words are written into the
 * chat box (after anything already typed) for the person to read, fix and send - nothing is
 * sent by itself. Recorded in the browser, transcribed by the server (app/speech.py, OpenAI;
 * English and Spanish detected by themselves). The recording is not kept anywhere.
 */
export function MicButton({ value, onChange, disabled, size = 32 }: {
  value: string; onChange: (v: string) => void; disabled?: boolean; size?: number
}) {
  const [state, setState] = useState<'idle' | 'recording' | 'working'>('idle')
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const timer = useRef<number | null>(null)
  const latest = useRef(value)
  latest.current = value

  const stopTimer = () => {
    if (timer.current !== null) { window.clearInterval(timer.current); timer.current = null }
  }
  useEffect(() => () => {
    stopTimer()
    if (rec.current && rec.current.state !== 'inactive') rec.current.stop()
  }, [])

  const supported = typeof window !== 'undefined' && !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== 'undefined'

  async function start() {
    setError(null)
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('The browser did not allow the microphone. Allow it in the address bar and try again.')
      return
    }
    const type = RECORDER_TYPES.find((t) => MediaRecorder.isTypeSupported(t))
    const r = new MediaRecorder(stream, type ? { mimeType: type } : undefined)
    const chunks: Blob[] = []
    r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
    r.onstop = async () => {
      stopTimer()
      stream.getTracks().forEach((t) => t.stop())
      const blob = new Blob(chunks, { type: r.mimeType || type || 'audio/webm' })
      if (!blob.size) { setState('idle'); return }
      setState('working')
      try {
        const { text } = await transcribeSpeech(blob)
        if (text.trim()) onChange(joinSpoken(latest.current, text))
        else setError('Nothing was heard. Try again a little closer to the microphone.')
      } catch (e) {
        setError(e instanceof Error ? e.message : 'It could not be heard.')
      }
      setState('idle')
    }
    rec.current = r
    r.start()
    setSeconds(0)
    setState('recording')
    const began = Date.now()
    timer.current = window.setInterval(() => {
      const s = (Date.now() - began) / 1000
      setSeconds(s)
      if (s >= MAX_SECONDS && r.state === 'recording') r.stop()
    }, 250)
  }

  if (!supported) return null
  const recording = state === 'recording'
  const label = recording ? 'Stop and write what I said'
    : state === 'working' ? 'Writing what you said…' : 'Speak instead of typing'
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
      <button type="button" aria-label={label} title={label}
        disabled={disabled || state === 'working'}
        onClick={() => (recording ? rec.current?.stop() : start())}
        style={{ width: size, height: size, borderRadius: size / 3, display: 'inline-flex',
          alignItems: 'center', justifyContent: 'center', border: '1px solid rgb(234,236,240)',
          background: recording ? 'rgb(217,45,32)' : '#fff',
          color: recording ? '#fff' : state === 'working' ? 'rgb(152,162,179)' : 'rgb(52,64,84)' }}>
        <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {recording ? <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" />
            : <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>}
        </svg>
      </button>
      {recording && <span style={{ fontSize: 12, color: 'rgb(217,45,32)' }}>{clock(seconds)}</span>}
      {state === 'working' && <span style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>Writing…</span>}
      {error && (
        <span role="alert" onClick={() => setError(null)}
          style={{ position: 'absolute', bottom: size + 6, right: 0, width: 260, zIndex: 20,
            fontSize: 12, color: 'rgb(180,35,24)', background: '#fff', border: '1px solid rgb(254,205,202)',
            borderRadius: 8, padding: '6px 8px', boxShadow: '0 4px 8px rgba(16,24,40,0.08)' }}>{error}</span>
      )}
    </span>
  )
}
