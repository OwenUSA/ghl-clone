// The two requests the browser softphone makes, and their shapes.
//
// Deliberately NOT added to api.ts. That file is being edited on another branch
// (feature/conversations-live) and a softphone is a self-contained surface, so the
// fetches live here. What is NOT duplicated is the part that matters: `refresh()` is
// imported from api.ts, so a credential fetch shares the SAME single-flight token
// renewal as every other request. Two competing refreshes rotate the refresh cookie
// concurrently and sign the user out -- which, for a softphone, would mean the browser
// quietly stops being a phone.
import { ApiError, Unauthorized, refresh } from './api'

/** Read a non-httpOnly cookie (the double-submit CSRF token). */
function cookie(name: string): string | null {
  const hit = document.cookie.split('; ').find((c) => c.startsWith(name + '='))
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null
}

async function request<T>(path: string, method: 'GET' | 'POST', retry = true): Promise<T> {
  const r = await fetch(path, {
    method,
    credentials: 'include',
    // Cookie auth is ambient, so every write carries the double-submit token.
    headers: method === 'POST' ? { 'X-CSRF-Token': cookie('ghl_csrf') ?? '' } : {},
  })
  if (r.status === 401) {
    if (retry && (await refresh())) return request<T>(path, method, false)
    window.dispatchEvent(new Event('ghl:unauthorized'))
    throw new Unauthorized('not signed in')
  }
  if (!r.ok) {
    let detail = ''
    try {
      detail = String((JSON.parse(await r.text()) as { detail?: unknown }).detail ?? '')
    } catch {
      // Not a FastAPI error body -- the status alone has to carry the meaning.
    }
    throw new ApiError(r.status, detail || `the softphone request failed (${r.status})`)
  }
  return r.json() as Promise<T>
}

/**
 * What the phone system hands back. `sip.password` is a real SIP digest password and
 * is the one secret this app deliberately puts in a browser -- a WebRTC endpoint
 * registers from the browser, so there is nowhere else it could live. It is
 * short-lived (OWEN caps it well below its own operator TTL) and must never be
 * logged, stored, or put in a URL.
 */
export type SoftphoneCredentials = {
  /** The OWEN operator this browser registers as, e.g. `owen-dreamteamroofingfl.com`. */
  operator: string
  /** `operator-<slug>` -- the PJSIP endpoint name the ring group dials. */
  endpoint: string
  sip: {
    endpoint: string
    username: string
    authorization_username: string
    password: string
    domain: string
    wss_url: string
    /** Unix seconds. Re-mint before this or the registration lapses. */
    expires_at: number
  }
  ice_servers: { urls: string[]; username?: string; credential?: string }[]
}

export type CallerContact = {
  id: number
  name: string | null
  phone: string | null
  business_name: string | null
}

export type CallerLookup = { number: string; contact: CallerContact | null }

export const fetchSoftphoneCredentials = () =>
  request<SoftphoneCredentials>('/api/softphone/credentials', 'POST')

/** Who is calling. Best-effort by contract: an unknown number resolves to `null`. */
export const lookupCaller = (number: string) =>
  request<CallerLookup>(`/api/softphone/caller?number=${encodeURIComponent(number)}`, 'GET')

/**
 * A phone number as a person reads it. Falls back to exactly what arrived, because a
 * caller-ID that is not a NANP number (an international caller, `anonymous`) still has
 * to be shown -- the one thing the card must never do is render nothing.
 */
export function formatPhone(raw: string | null | undefined): string {
  const text = String(raw || '').trim()
  const d = text.replace(/\D/g, '')
  if (d.length === 11 && d.startsWith('1')) {
    return `(${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7)}`
  }
  if (d.length === 10) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`
  return text
}
