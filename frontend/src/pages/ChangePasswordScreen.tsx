import { useState } from 'react'
import { changePassword, logout, type Me } from '../lib/auth'

/**
 * The first screen after an admin creates an account or resets its password (My Staff,
 * 2026-09-15). Nothing else is drawn, and nothing else would work: the API refuses
 * every other request with "choose a new password" until this succeeds
 * (`auth.PASSWORD_CHANGE_PATHS`).
 *
 * OUR design — GoHighLevel sends an email invitation instead, which the owner ruled out.
 * It is the sign-in card, in the sign-in card's measured colours, so it reads as part of
 * signing in rather than as a page of the app.
 */
export function ChangePasswordScreen({ user, onChanged }: { user: Me; onChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const problem = next.length > 0 && next.length < 8 ? 'At least 8 characters.'
    : confirm.length > 0 && confirm !== next ? 'The two new passwords do not match.'
      : next.length > 0 && next === current ? 'Choose a password different from the one you were given.'
        : null
  const ready = !!current && next.length >= 8 && confirm === next && next !== current

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!ready) return
    setBusy(true)
    setError(null)
    try {
      await changePassword(current, next)
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const label: React.CSSProperties = {
    display: 'block', fontSize: 13, fontWeight: 500, color: 'rgb(52,64,84)',
    marginTop: 16, marginBottom: 6,
  }
  const field: React.CSSProperties = {
    width: '100%', height: 40, padding: '0 12px', border: '1px solid rgb(234,236,240)',
    borderRadius: 8, fontSize: 14, color: 'rgb(16,24,40)', outline: 'none',
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100vh', width: '100vw', background: 'rgb(249,250,251)' }}>
      <form onSubmit={submit} aria-label="Choose a new password"
        style={{ width: 380, padding: 32, background: 'white',
          border: '1px solid rgb(234,236,240)', borderRadius: 12 }}>
        <div style={{ fontSize: 20, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          Choose a new password
        </div>
        <div style={{ fontSize: 14, color: 'rgb(102,112,133)', marginTop: 4, lineHeight: 1.5 }}>
          Hi {user.name.split(' ')[0]} — an admin set the password you signed in with.
          Choose your own to continue.
        </div>

        {/* A hidden username field lets a password manager file the new password under
            the right account. */}
        <input type="email" name="email" autoComplete="username" value={user.email ?? ''}
          readOnly hidden />

        <label style={label} htmlFor="pw-current">Password you were given</label>
        <input id="pw-current" name="current-password" type="password"
          autoComplete="current-password" autoFocus required value={current}
          onChange={(e) => setCurrent(e.target.value)} style={field} />

        <label style={label} htmlFor="pw-new">New password</label>
        <input id="pw-new" name="new-password" type="password" autoComplete="new-password"
          required value={next} onChange={(e) => setNext(e.target.value)} style={field} />

        <label style={label} htmlFor="pw-confirm">Confirm new password</label>
        <input id="pw-confirm" name="confirm-password" type="password"
          autoComplete="new-password" required value={confirm}
          onChange={(e) => setConfirm(e.target.value)} style={field} />

        {(error || problem) && (
          <div role="alert" style={{ marginTop: 16, padding: '8px 12px', borderRadius: 8,
            fontSize: 13, color: 'rgb(180,35,24)', background: 'rgb(254,243,242)' }}>
            {error ?? problem}
          </div>
        )}

        <button type="submit" disabled={!ready || busy}
          style={{ width: '100%', height: 40, marginTop: 24, border: 'none', borderRadius: 8,
            background: 'rgb(0,78,235)', color: 'white', fontSize: 14, fontWeight: 500,
            cursor: ready && !busy ? 'pointer' : 'not-allowed',
            opacity: ready && !busy ? 1 : 0.6 }}>
          {busy ? 'Saving…' : 'Set password and continue'}
        </button>
        <button type="button"
          onClick={async () => { await logout(); window.location.reload() }}
          style={{ width: '100%', marginTop: 12, fontSize: 13, color: 'rgb(102,112,133)',
            background: 'transparent', border: 'none', cursor: 'pointer' }}>
          Sign out
        </button>
      </form>
    </div>
  )
}
