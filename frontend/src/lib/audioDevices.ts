// Which microphone and speaker this browser's calls use.
//
// Ported from owen-main (`frontend/src/lib/audioDevices.ts`), without its ringtone: the
// CRM's incoming-call card does not ring a separate device. Two per-browser preferences,
// applied at two seams:
//   - microphone -> the getUserMedia constraint a call answers with, and a live track
//                   swap on the call's RTCPeerConnection when it changes mid-call;
//   - speaker    -> HTMLMediaElement.setSinkId on the remote-audio element (Chromium
//                   only; elsewhere the choice is kept and the system default plays).
//
// `ideal`, never `exact`, for the microphone — owen-main learned this in production: a
// saved device id goes stale when site data is cleared or a headset is unplugged, and
// `exact` then makes getUserMedia throw, which SIP.js turns into a 480 that kills the
// call. `ideal` falls back to the default microphone instead.
import { useCallback, useEffect, useState } from 'react'

export type AudioKind = 'mic' | 'speaker'

const KEY: Record<AudioKind, string> = {
  mic: 'ghl.audio.mic',
  speaker: 'ghl.audio.speaker',
}

export function getAudioPref(kind: AudioKind): string {
  try {
    return window.localStorage.getItem(KEY[kind]) || ''
  } catch {
    return ''
  }
}

export function setAudioPref(kind: AudioKind, deviceId: string): void {
  try {
    if (deviceId) window.localStorage.setItem(KEY[kind], deviceId)
    else window.localStorage.removeItem(KEY[kind])
  } catch {
    /* private mode: the choice lasts for this page only */
  }
}

/** The audio constraint a call is answered with. `true` = the system default. */
export function micConstraint(): MediaTrackConstraints | boolean {
  const id = getAudioPref('mic')
  return id ? { deviceId: { ideal: id } } : true
}

/** Can this browser route call audio to a chosen output at all? */
export function canPickSpeaker(): boolean {
  return typeof HTMLMediaElement !== 'undefined'
    && typeof (HTMLMediaElement.prototype as { setSinkId?: unknown }).setSinkId === 'function'
}

/** Route an <audio> element to the saved speaker. Best effort, never throws. */
export async function applySpeaker(el: HTMLAudioElement | null): Promise<void> {
  const sink = (el as unknown as { setSinkId?: (id: string) => Promise<void> } | null)?.setSinkId
  if (!el || typeof sink !== 'function') return
  try {
    await sink.call(el, getAudioPref('speaker') || 'default')
  } catch {
    /* the device went away — leave it on whatever it is playing to */
  }
}

export type DeviceOption = { deviceId: string; label: string }

/**
 * The microphones and speakers this browser can see. Labels are only readable once the
 * microphone has been allowed, which a browser phone that is Ready already has.
 */
export function useAudioDevices(active: boolean) {
  const [inputs, setInputs] = useState<DeviceOption[]>([])
  const [outputs, setOutputs] = useState<DeviceOption[]>([])

  const refresh = useCallback(async () => {
    try {
      const list = await navigator.mediaDevices.enumerateDevices()
      const pick = (kind: MediaDeviceKind, fallback: string) =>
        // 'default' and 'communications' are aliases for a device already listed, and
        // "System default" is offered separately.
        list.filter((d) => d.kind === kind && d.deviceId && d.deviceId !== 'default'
          && d.deviceId !== 'communications')
          .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `${fallback} ${i + 1}` }))
      setInputs(pick('audioinput', 'Microphone'))
      setOutputs(pick('audiooutput', 'Speaker'))
    } catch {
      setInputs([])
      setOutputs([])
    }
  }, [])

  useEffect(() => {
    if (!active || !navigator.mediaDevices?.enumerateDevices) return
    void refresh()
    const onChange = () => void refresh()
    navigator.mediaDevices.addEventListener?.('devicechange', onChange)
    return () => navigator.mediaDevices.removeEventListener?.('devicechange', onChange)
  }, [active, refresh])

  return { inputs, outputs }
}
