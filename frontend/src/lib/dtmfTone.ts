// What a keypad press SOUNDS like to the operator.
//
// Ported from owen-main (`frontend/src/lib/dtmfTone.ts`). `sendDtmf` sends the tone to the
// far end (RFC 2833) and plays nothing locally, so without this a press feels dead. This
// plays the real dual tone -- the same two frequencies a phone plays -- short and quiet,
// through Web Audio. Feedback only: any audio failure is swallowed and never touches the
// call.

const DTMF: Record<string, [number, number]> = {
  '1': [697, 1209], '2': [697, 1336], '3': [697, 1477],
  '4': [770, 1209], '5': [770, 1336], '6': [770, 1477],
  '7': [852, 1209], '8': [852, 1336], '9': [852, 1477],
  '*': [941, 1209], '0': [941, 1336], '#': [941, 1477],
}

/** The two frequencies for a key, or null. Exported for the tests. */
export function dtmfFrequencies(digit: string): [number, number] | null {
  return DTMF[digit] ?? null
}

let ctx: AudioContext | null = null

function audioContext(): AudioContext | null {
  try {
    const Ctor = window.AudioContext
      ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return null
    if (!ctx) ctx = new Ctor()
    // A keypad press is a user gesture, so resuming a suspended context here works.
    if (ctx.state === 'suspended') void ctx.resume()
    return ctx
  } catch {
    return null
  }
}

/** Play the tone for `digit` for ~140ms. Does nothing for anything that is not a key. */
export function playDtmfTone(digit: string, durationMs = 140): void {
  const pair = dtmfFrequencies(digit)
  if (!pair) return
  const ac = audioContext()
  if (!ac) return
  try {
    const now = ac.currentTime
    const end = now + durationMs / 1000
    const gain = ac.createGain()
    gain.gain.setValueAtTime(0, now)
    gain.gain.linearRampToValueAtTime(0.14, now + 0.012)
    gain.gain.setValueAtTime(0.14, end - 0.02)
    gain.gain.linearRampToValueAtTime(0, end)
    gain.connect(ac.destination)
    for (const freq of pair) {
      const osc = ac.createOscillator()
      osc.type = 'sine'
      osc.frequency.value = freq
      osc.connect(gain)
      osc.start(now)
      osc.stop(end)
    }
  } catch {
    /* feedback only */
  }
}
