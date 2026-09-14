import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CompanyCamSettings } from '../components/CompanyCamSettings'
import { CustomFieldsPanel } from '../components/CustomFieldsPanel'
import { SETTINGS_SECTIONS, sectionFromPath, type SettingsSection } from '../lib/settingsSections'
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
 * Settings, in two sections.
 *
 * **Custom Fields** — the owner's job questions. The panel is `CustomFieldsPanel`,
 * mounted here unchanged; until 2026-09-13 it was a tab on Opportunities, which
 * GoHighLevel does not have. Reachable by clicking the section or by loading
 * /settings/custom-fields.
 */
export function SettingsPage({ user }: { user: Me }) {
  const [section, setSection] = useState<SettingsSection>(
    () => sectionFromPath(window.location.pathname))

  const choose = (key: SettingsSection) => {
    setSection(key)
    const path = SETTINGS_SECTIONS.find((s) => s.key === key)?.path ?? '/settings'
    // Replace, not push: a section switch is not a page the Back button should replay.
    if (window.location.pathname.startsWith('/settings')) window.history.replaceState(null, '', path)
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col" style={{ height: '100vh', overflow: 'hidden' }}>
      <div className="shrink-0" style={{ padding: '32px 32px 0' }}>
        <div style={{ fontSize: 20, fontWeight: 600, color: INK }}>Settings</div>
        <div style={{ fontSize: 14, color: MUTED, marginTop: 4 }}>
          {user.name} · {user.role}
        </div>
        <div role="tablist" aria-label="Settings sections" className="flex gap-6"
          style={{ marginTop: 20, borderBottom: `1px solid ${LINE}` }}>
          {SETTINGS_SECTIONS.map((s) => (s.key !== 'companycam' || user.role === 'ADMIN') && (
            <button key={s.key} role="tab" aria-selected={section === s.key}
              onClick={() => choose(s.key)}
              style={{
                height: 38, fontSize: 14, fontWeight: 500,
                color: section === s.key ? 'rgb(56,160,219)' : 'rgb(71,84,103)',
                borderBottom: '2px solid ' + (section === s.key ? 'rgb(56,160,219)' : 'transparent'),
              }}>
              {s.label}
            </button>
          ))}
        </div>
      </div>
      {section === 'custom-fields'
        ? <div className="flex min-h-0 flex-1 flex-col" style={{ padding: '16px 16px 0' }}>
            <CustomFieldsPanel user={user} />
          </div>
        : section === 'companycam' && user.role === 'ADMIN' ? <CompanyCamSettings />
        : <AccountSettings />}
    </div>
  )
}

/**
 * Account settings: API tokens and password.
 *
 * The token panel is how the `ghl` CLI gets its credential without anyone
 * pasting a password into a terminal — and how a leaked one is revoked.
 */
function AccountSettings() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  // The plaintext is kept with its id so revoking that token can clear the
  // panel -- the secret on screen is dead the moment the row says `revoked`.
  const [minted, setMinted] = useState<{ id: number; token: string } | null>(null)
  const [copied, setCopied] = useState(false)

  const tokens = useQuery({ queryKey: ['tokens'], queryFn: listTokens })

  const mint = useMutation({
    mutationFn: () => createToken(name.trim() || 'cli', 365),
    onSuccess: (t) => {
      setMinted({ id: t.id, token: t.token })
      setName('')
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
  })

  const revoke = useMutation({
    mutationFn: revokeToken,
    onSuccess: (_result, id) => {
      // Revoking the token the panel is displaying leaves a dead credential on
      // screen above a row that reads `revoked`. Drop it.
      if (minted?.id === id) {
        setMinted(null)
        setCopied(false)
      }
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
  })

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 32 }}>
      <section style={{ maxWidth: 860 }}>
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
              {minted.token}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(minted.token)
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
            {(mint.error ?? revoke.error ?? tokens.error)?.message}
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
                      // Revocation is irreversible -- only a sha256 is stored, so a
                      // mis-click destroys the credential for good. The CLI already
                      // makes you pass --yes for this; the one-click button did not.
                      onClick={() => {
                        if (window.confirm(
                          `Revoke "${t.name}"? It stops working immediately and `
                          + 'cannot be restored.')) revoke.mutate(t.id)
                      }}
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
