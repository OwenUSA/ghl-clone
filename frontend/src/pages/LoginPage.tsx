import { useState } from 'react'
import { login } from '../lib/auth'

/**
 * Sign-in screen.
 *
 * Colours are the measured GHL palette used across the rest of the app
 * (rgb(0,78,235) primary, rgb(16,24,40) heading, rgb(102,112,133) muted) rather
 * than anything new — see DECISIONS.md on generating the design system from
 * measured usage.
 */
export function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email.trim(), password)
      onSignedIn()
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      // A dead backend and a wrong password are different problems, and telling
      // someone "invalid email or password" when the server is down sends them
      // hunting for the wrong thing.
      setError(
        message === 'Failed to fetch'
          ? 'Cannot reach the server. Is the backend running on port 8000?'
          : message,
      )
    } finally {
      setBusy(false)
    }
  }

  const field: React.CSSProperties = {
    width: '100%',
    height: 40,
    padding: '0 12px',
    border: '1px solid rgb(234,236,240)',
    borderRadius: 8,
    fontSize: 14,
    color: 'rgb(16,24,40)',
    outline: 'none',
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        width: '100vw',
        background: 'rgb(249,250,251)',
      }}
    >
      <form
        onSubmit={submit}
        style={{
          width: 360,
          padding: 32,
          background: 'white',
          border: '1px solid rgb(234,236,240)',
          borderRadius: 12,
        }}
      >
        <div style={{ fontSize: 20, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          Dream Team Roofing
        </div>
        <div style={{ fontSize: 14, color: 'rgb(102,112,133)', marginTop: 4 }}>
          Sign in to continue
        </div>

        <label
          style={{
            display: 'block',
            fontSize: 13,
            fontWeight: 500,
            color: 'rgb(52,64,84)',
            marginTop: 24,
            marginBottom: 6,
          }}
        >
          Email
        </label>
        <input
          type="email"
          autoFocus
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={field}
        />

        <label
          style={{
            display: 'block',
            fontSize: 13,
            fontWeight: 500,
            color: 'rgb(52,64,84)',
            marginTop: 16,
            marginBottom: 6,
          }}
        >
          Password
        </label>
        <input
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={field}
        />

        {error && (
          <div
            role="alert"
            style={{
              marginTop: 16,
              padding: '8px 12px',
              borderRadius: 8,
              fontSize: 13,
              color: 'rgb(180,35,24)',
              background: 'rgb(254,243,242)',
            }}
          >
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          style={{
            width: '100%',
            height: 40,
            marginTop: 24,
            border: 'none',
            borderRadius: 8,
            background: 'rgb(0,78,235)',
            color: 'white',
            fontSize: 14,
            fontWeight: 500,
            cursor: busy ? 'default' : 'pointer',
            opacity: busy ? 0.6 : 1,
          }}
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
