/**
 * Auth calls.
 *
 * Deliberately does NOT go through api.ts's `get`/`send`: those dispatch
 * `ghl:unauthorized` on a 401, which would be circular for the login request
 * itself. It does share api.ts's single-flight `refresh` -- see `me()`.
 */
import { refresh } from './api'

export type Me = {
  id: number
  name: string
  email: string | null
  role: 'ADMIN' | 'DISPATCHER' | 'TECH'
  is_active: boolean
}

export type ApiToken = {
  id: number
  name: string
  prefix: string
  scopes: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
}

function csrf(): string {
  const hit = document.cookie
    .split('; ')
    .find((c) => c.startsWith('ghl_csrf='))
  return hit ? decodeURIComponent(hit.slice('ghl_csrf='.length)) : ''
}

/** An error that remembers its HTTP status, so callers can branch on 401. */
export class HttpError extends Error {
  // A plain field, not a constructor parameter property: tsconfig sets
  // erasableSyntaxOnly, which forbids the shorthand.
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-CSRF-Token': csrf() }
  if (body) headers['Content-Type'] = 'application/json'
  const r = await fetch(path, {
    method,
    credentials: 'include',
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let detail = `${r.status}`
    try {
      detail = (await r.json()).detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new HttpError(r.status, detail)
  }
  return (r.status === 204 ? null : await r.json()) as T
}

export const login = (email: string, password: string) =>
  call<{ user: Me }>('/api/auth/login', 'POST', { email, password })

export const logout = () => call<{ ok: boolean }>('/api/auth/logout', 'POST')

/**
 * Who am I? This one query gates the entire app (App.tsx), so it is the one that
 * must survive an expired access token.
 *
 * The access token lasts 15 minutes. Leave the tab for longer than that -- switch
 * to another app, and on mobile the tab is often discarded and reloaded on return
 * -- and this call 401s. Without the retry below it threw straight away, App fell
 * back to <LoginPage/>, and the user was silently signed out with an empty form,
 * despite holding a refresh token that was good for another seven days.
 *
 * Every other request already renewed the token this way through api.ts's get();
 * this was the only one that did not.
 */
export async function me(): Promise<{ user: Me; kind: string; scopes: string[] }> {
  const path = '/api/auth/me'
  try {
    return await call<{ user: Me; kind: string; scopes: string[] }>(path)
  } catch (err) {
    // Only a 401 is worth retrying: a 403, a 500 or a dead backend will not be
    // fixed by a new access token, and retrying would just hide the real error.
    // Branch on the status, not the message -- the backend has 13 distinct 401
    // details ("access token expired", "invalid token", ...) and matching text
    // would quietly miss most of them.
    if (!(err instanceof HttpError) || err.status !== 401) throw err
    if (!(await refresh())) throw err
    return await call<{ user: Me; kind: string; scopes: string[] }>(path)
  }
}

export const changePassword = (current_password: string, new_password: string) =>
  call<{ ok: boolean }>('/api/auth/password', 'POST', {
    current_password,
    new_password,
  })

export const listTokens = () => call<ApiToken[]>('/api/auth/tokens')

export const createToken = (name: string, expires_in_days: number | null) =>
  call<ApiToken & { token: string }>('/api/auth/tokens', 'POST', {
    name,
    expires_in_days,
  })

export const revokeToken = (id: number) =>
  call<{ ok: boolean }>(`/api/auth/tokens/${id}`, 'DELETE')
