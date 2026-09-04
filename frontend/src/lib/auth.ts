/**
 * Auth calls.
 *
 * Deliberately does NOT go through api.ts's helpers: those redirect to the login
 * screen on 401, which would be circular for the login request itself.
 */

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
    throw new Error(detail)
  }
  return (r.status === 204 ? null : await r.json()) as T
}

export const login = (email: string, password: string) =>
  call<{ user: Me }>('/api/auth/login', 'POST', { email, password })

export const logout = () => call<{ ok: boolean }>('/api/auth/logout', 'POST')

export const me = () =>
  call<{ user: Me; kind: string; scopes: string[] }>('/api/auth/me')

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
