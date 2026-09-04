import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  changePassword,
  createToken,
  listTokens,
  revokeToken,
  type ApiToken,
  type Me,
} from '../lib/auth'

const MUTED = 'rgb(102,112,133)'
const INK = 'rgb(16,24,40)'
const LINE = 'rgb(234,236,240)'
const BLUE = 'rgb(0,78,235)'

function when(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

function status(t: ApiToken) {
  if (t.revoked_at) return 'revoked'
  if (t.expires_at && new Date(t.expires_at) < new Date()) return 'expired'
  return 'active'
}

/**
 * Account settings: API tokens and password.
 *
 * The token panel is how the `ghl` CLI gets its credential without anyone
 * pasting a password into a terminal — and how a leaked one is revoked.
 */
export function SettingsPage({ user }: { user: Me }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [minted, setMinted] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const tokens = useQuery({ queryKey: ['tokens'], queryFn: listTokens })

  const mint = useMutation({
    mutationFn: () => createToken(name.trim() || 'cli', 365),
    onSuccess: (t) => {
      setMinted(t.token)
      setName('')
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
  })

  const revoke = useMutation({
    mutationFn: revokeToken,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tokens'] }),
  })

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 32 }}>
      <div style={{ fontSize: 20, fontWeight: 600, color: INK }}>Settings</div>
      <div style={{ fontSize: 14, color: MUTED, marginTop: 4 }}>
        {user.name} · {user.role}
      </div>

      <section style={{ marginTop: 32, maxWidth: 860 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>API tokens</div>
        <div style={{ fontSize: 13, color: MUTED, marginTop: 4, lineHeight: 1.5 }}>
          Used by the <code>ghl</code> command-line tool. A token carries your role,
          so treat it like your password — and revoke it here if it leaks, which
          does not change your password or sign you out.
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <input
            placeholder="Token name, e.g. laptop-cli"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{
              flex: 1,
              maxWidth: 320,
              height: 36,
              padding: '0 12px',
              border: `1px solid ${LINE}`,
              borderRadius: 8,
              fontSize: 13,
              outline: 'none',
            }}
          />
          <button
            onClick={() => mint.mutate()}
            disabled={mint.isPending}
            style={{
              height: 36,
              padding: '0 16px',
              border: 'none',
              borderRadius: 8,
              background: BLUE,
              color: 'white',
              fontSize: 13,
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Create token
          </button>
        </div>

        {minted && (
          <div
            style={{
              marginTop: 16,
              padding: 16,
              border: `1px solid ${LINE}`,
              borderRadius: 8,
              background: 'rgb(239,244,255)',
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 500, color: INK }}>
              Copy this now — it is not shown again.
            </div>
            <div
              style={{
                marginTop: 8,
                padding: 10,
                background: 'white',
                border: `1px solid ${LINE}`,
                borderRadius: 6,
                fontFamily: 'monospace',
                fontSize: 12,
                wordBreak: 'break-all',
                color: INK,
              }}
            >
              {minted}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(minted)
                  setCopied(true)
                }}
                style={{
                  height: 30,
                  padding: '0 12px',
                  border: `1px solid ${LINE}`,
                  borderRadius: 6,
                  background: 'white',
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                {copied ? 'Copied' : 'Copy'}
              </button>
              <button
                onClick={() => {
                  setMinted(null)
                  setCopied(false)
                }}
                style={{
                  height: 30,
                  padding: '0 12px',
                  border: 'none',
                  background: 'transparent',
                  fontSize: 12,
                  color: MUTED,
                  cursor: 'pointer',
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {(mint.isError || revoke.isError || tokens.isError) && (
          <div style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>
            {String(mint.error ?? revoke.error ?? tokens.error)}
          </div>
        )}

        <table
          style={{
            width: '100%',
            marginTop: 24,
            borderCollapse: 'collapse',
            fontSize: 13,
          }}
        >
          <thead>
            <tr style={{ textAlign: 'left', color: MUTED }}>
              {['Name', 'Prefix', 'Scopes', 'Last used', 'Status', ''].map((h) => (
                <th
                  key={h}
                  style={{ padding: '8px 0', borderBottom: `1px solid ${LINE}`, fontWeight: 500 }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(tokens.data ?? []).map((t) => (
              <tr key={t.id} style={{ color: INK }}>
                <td style={{ padding: '10px 0', borderBottom: `1px solid ${LINE}` }}>
                  {t.name}
                </td>
                <td
                  style={{
                    padding: '10px 0',
                    borderBottom: `1px solid ${LINE}`,
                    fontFamily: 'monospace',
                    fontSize: 12,
                  }}
                >
                  {t.prefix}…
                </td>
                <td style={{ padding: '10px 0', borderBottom: `1px solid ${LINE}` }}>
                  {t.scopes || 'full'}
                </td>
                <td style={{ padding: '10px 0', borderBottom: `1px solid ${LINE}` }}>
                  {when(t.last_used_at)}
                </td>
                <td style={{ padding: '10px 0', borderBottom: `1px solid ${LINE}` }}>
                  {status(t)}
                </td>
                <td
                  style={{
                    padding: '10px 0',
                    borderBottom: `1px solid ${LINE}`,
                    textAlign: 'right',
                  }}
                >
                  {status(t) === 'active' && (
                    <button
                      onClick={() => revoke.mutate(t.id)}
                      style={{
                        border: 'none',
                        background: 'transparent',
                        color: 'rgb(180,35,24)',
                        fontSize: 12,
                        cursor: 'pointer',
                      }}
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {tokens.data?.length === 0 && (
              <tr>
                <td colSpan={6} style={{ padding: '16px 0', color: MUTED }}>
                  No tokens yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <PasswordPanel />
    </div>
  )
}

function PasswordPanel() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [done, setDone] = useState(false)

  const change = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => {
      setDone(true)
      setCurrent('')
      setNext('')
    },
  })

  const field: React.CSSProperties = {
    width: 240,
    height: 36,
    padding: '0 12px',
    border: `1px solid ${LINE}`,
    borderRadius: 8,
    fontSize: 13,
    outline: 'none',
  }

  return (
    <section style={{ marginTop: 48, maxWidth: 860 }}>
      <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>Password</div>
      <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>
        Changing it signs you out of every other browser. API tokens keep working.
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 16, alignItems: 'center' }}>
        <input
          type="password"
          placeholder="Current password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          style={field}
        />
        <input
          type="password"
          placeholder="New password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          style={field}
        />
        <button
          onClick={() => change.mutate()}
          disabled={!current || next.length < 8 || change.isPending}
          style={{
            height: 36,
            padding: '0 16px',
            border: `1px solid ${LINE}`,
            borderRadius: 8,
            background: 'white',
            fontSize: 13,
            cursor: 'pointer',
          }}
        >
          Change
        </button>
      </div>
      {change.isError && (
        <div style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>
          {String(change.error)}
        </div>
      )}
      {done && (
        <div style={{ marginTop: 12, fontSize: 13, color: 'rgb(2,122,72)' }}>
          Password changed.
        </div>
      )}
    </section>
  )
}
