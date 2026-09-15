import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  listStaff, patchStaffUser, type StaffUser,
} from '../lib/api'
import type { Me } from '../lib/auth'
import {
  ROLE_LABELS, deactivateBlock, initials, pageOf, roleLabel,
} from '../lib/access'
import { IconPencil, IconPlus, IconSearchSmall, IconTrashOutline } from './PipelineIcons'
import { StaffUserModal } from './StaffUserModal'
import { Chevron } from './opportunity/ui'

const INK = 'rgb(16,24,40)'
const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'
const FIELD_LINE = 'rgb(208,213,221)'
const BLUE = 'rgb(21,94,239)'
const PER_PAGE = 10

/** GoHighLevel draws each avatar in its own soft colour (screenshot 42). */
const AVATARS = ['rgb(132,204,22)', 'rgb(45,212,191)', 'rgb(190,200,55)', 'rgb(74,222,128)',
  'rgb(96,165,250)', 'rgb(251,146,60)']

/**
 * Settings → My Staff — GoHighLevel's staff list (refs/round3/42), ADMIN only.
 *
 *   [User Role ▾] [🔍 name, email, phone, ids] [      + Add User      ]
 *   Name              Email                    Phone         User Type    Action
 *   (AB) Antonio …    antonio@…                               TECHNICIAN   ✎ 🗑
 *                     iaR65V… ⧉ (the user id, with a copy button)
 *   Page 1                                              Previous [1] Next
 *
 * The trash icon DEACTIVATES — nobody is ever deleted: they cannot sign in, every
 * session and token ends, and their name stays on their jobs, notes and tasks. A
 * deactivated row shows so, with a Reactivate icon in its place. Machine accounts (no
 * password — the telephony feed) are listed separately and are never edited into a
 * login. Search and the role filter are the server's (`GET /api/users?q=&role=`).
 */
export function MyStaffPanel({ user }: { user: Me }) {
  const qc = useQueryClient()
  const [role, setRole] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [dialog, setDialog] = useState<{ kind: 'add' } | { kind: 'edit'; row: StaffUser } | null>(null)
  const [confirm, setConfirm] = useState<StaffUser | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<number | null>(null)

  const filtered = useQuery({
    queryKey: ['staff', q.trim(), role],
    queryFn: () => listStaff({ q, role }),
  })
  // The whole roster, unfiltered, for the last-admin and self rules — a search must
  // not make the last admin look like one of several.
  const everyone = useQuery({ queryKey: ['staff', '', ''], queryFn: () => listStaff() })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['staff'] })
    qc.invalidateQueries({ queryKey: ['users'] })
    qc.invalidateQueries({ queryKey: ['calendars'] })
  }

  const setActive = useMutation({
    mutationFn: ({ row, active }: { row: StaffUser; active: boolean }) =>
      patchStaffUser(row.id, { is_active: active }),
    onSuccess: (u) => {
      setConfirm(null)
      setError(null)
      setNotice(u.is_active ? `${u.name} can sign in again.`
        : `${u.name} is deactivated. Their work stays attributed to them.`)
      refresh()
    },
    onError: (e: Error) => { setConfirm(null); setError(e.message) },
  })

  const all = everyone.data ?? []
  const rows = filtered.data ?? []
  const people = rows.filter((r) => !r.machine)
  const machines = rows.filter((r) => r.machine)
  const { rows: slice, pages } = pageOf(people, page, PER_PAGE)
  const current = Math.min(page, pages)

  const copyId = async (id: number) => {
    try {
      await navigator.clipboard.writeText(String(id))
      setCopied(id)
      window.setTimeout(() => setCopied((c) => (c === id ? null : c)), 1500)
    } catch {
      window.prompt('Copy this user id', String(id))
    }
  }

  const head: React.CSSProperties = { fontSize: 12, fontWeight: 500, color: TEXT,
    textAlign: 'left', padding: '0 20px', height: 40, backgroundColor: 'rgb(249,250,251)',
    borderBottom: `1px solid ${LINE}` }
  const cell: React.CSSProperties = { padding: '12px 20px', borderBottom: `1px solid ${LINE}`,
    verticalAlign: 'middle' }

  const actions = (row: StaffUser) => {
    const block = row.is_active ? deactivateBlock(row, user.id, all) : null
    return (
      <div className="flex items-center gap-4">
        <button type="button" aria-label={`Edit ${row.name}`}
          disabled={row.machine}
          title={row.machine ? 'A machine account is managed with app.bootstrap, not edited here'
            : 'Edit'}
          onClick={() => { setError(null); setNotice(null); setDialog({ kind: 'edit', row }) }}
          style={{ opacity: row.machine ? 0.35 : 1, cursor: row.machine ? 'not-allowed' : 'pointer' }}>
          <IconPencil size={16} color={TEXT} />
        </button>
        {row.is_active ? (
          <button type="button" aria-label={`Deactivate ${row.name}`}
            disabled={!!block} title={block ?? 'Deactivate'}
            onClick={() => { setError(null); setNotice(null); setConfirm(row) }}
            style={{ opacity: block ? 0.35 : 1, cursor: block ? 'not-allowed' : 'pointer' }}>
            <IconTrashOutline size={16} color={TEXT} />
          </button>
        ) : (
          <button type="button" aria-label={`Reactivate ${row.name}`} title="Reactivate"
            disabled={setActive.isPending}
            onClick={() => setActive.mutate({ row, active: true })}>
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={BLUE}
              strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" />
            </svg>
          </button>
        )}
      </div>
    )
  }

  const nameCell = (row: StaffUser) => (
    <div className="flex items-center gap-3">
      <span aria-hidden="true" className="flex shrink-0 items-center justify-center"
        style={{ width: 32, height: 32, borderRadius: 16, fontSize: 12, fontWeight: 500,
          color: 'rgb(20,83,45)', backgroundColor: AVATARS[row.id % AVATARS.length],
          opacity: row.is_active ? 1 : 0.5 }}>
        {initials(row.name)}
      </span>
      <span style={{ fontSize: 14, color: row.is_active ? TEXT : MUTED, whiteSpace: 'nowrap' }}>
        {row.name}
      </span>
      {!row.is_active && (
        <span style={{ fontSize: 11, fontWeight: 500, color: 'rgb(180,35,24)',
          backgroundColor: 'rgb(254,243,242)', borderRadius: 10, padding: '1px 8px',
          whiteSpace: 'nowrap' }}>
          Deactivated
        </span>
      )}
      {row.is_active && row.must_change_password && (
        <span title="Signs in with the password an admin set, then must choose their own"
          style={{ fontSize: 11, fontWeight: 500, color: 'rgb(181,71,8)',
            backgroundColor: 'rgb(255,250,235)', borderRadius: 10, padding: '1px 8px',
            whiteSpace: 'nowrap' }}>
          Password change pending
        </span>
      )}
    </div>
  )

  const emailCell = (row: StaffUser) => (
    <div>
      <div style={{ fontSize: 14, color: TEXT }}>{row.email}</div>
      <div className="flex items-center gap-2" style={{ fontSize: 11, color: MUTED, marginTop: 2 }}>
        <span>{row.id}</span>
        <button type="button" aria-label={`Copy user id ${row.id}`}
          title={copied === row.id ? 'Copied' : 'Copy user id'} onClick={() => copyId(row.id)}>
          <svg width={13} height={13} viewBox="0 0 24 24" fill="none"
            stroke={copied === row.id ? BLUE : MUTED} strokeWidth={1.8} aria-hidden="true">
            <rect x="9" y="9" width="12" height="12" rx="2" />
            <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
          </svg>
        </button>
      </div>
    </div>
  )

  const typeCell = (row: StaffUser) => (
    <div>
      <span style={{ fontSize: 12, letterSpacing: '0.04em', color: TEXT }}>
        {roleLabel(row.role).toUpperCase()}
      </span>
      {row.only_assigned_data && row.role !== 'ADMIN' && (
        <div style={{ fontSize: 11, color: MUTED, marginTop: 2 }}>Only assigned data</div>
      )}
    </div>
  )

  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '16px 32px 32px' }}>
      {/* Screenshot 42's proportions: the three controls share the right ~70% of the row. */}
      <div className="flex items-center justify-end" style={{ marginBottom: 20, gap: 8 }}>
        <div className="relative" style={{ flex: '0 1 354px', minWidth: 160 }}>
          <select aria-label="User Role" value={role}
            onChange={(e) => { setRole(e.target.value); setPage(1) }}
            style={{ width: '100%', height: 36, borderRadius: 4, border: `1px solid ${FIELD_LINE}`,
              padding: '0 32px 0 10px', fontSize: 14, color: role ? INK : TEXT, appearance: 'none',
              backgroundColor: '#fff' }}>
            <option value="">User Role</option>
            {Object.entries(ROLE_LABELS).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          <span className="pointer-events-none absolute" style={{ right: 10, top: 10 }}>
            <Chevron />
          </span>
        </div>
        <label className="flex items-center gap-2" style={{ flex: '0 1 400px', minWidth: 180, height: 36,
          border: `1px solid ${FIELD_LINE}`, borderRadius: 4, padding: '0 10px', backgroundColor: '#fff' }}>
          <IconSearchSmall size={16} color={TEXT} />
          <input value={q} aria-label="Search staff" placeholder="name, email, phone, ids"
            onChange={(e) => { setQ(e.target.value); setPage(1) }}
            style={{ flex: 1, fontSize: 14, outline: 'none', color: INK }} />
        </label>
        <button type="button"
          onClick={() => { setError(null); setNotice(null); setDialog({ kind: 'add' }) }}
          className="flex items-center justify-center gap-1"
          style={{ flex: '0 1 400px', minWidth: 120, height: 36, borderRadius: 4, backgroundColor: BLUE, color: '#fff',
            fontSize: 14, fontWeight: 500 }}>
          <IconPlus size={15} color="#fff" /> Add User
        </button>
      </div>

      {(error || notice) && (
        <div role={error ? 'alert' : 'status'} className="flex items-start gap-3"
          style={{ marginBottom: 12, fontSize: 13, borderRadius: 8, padding: '8px 12px',
            color: error ? 'rgb(180,35,24)' : 'rgb(2,122,72)',
            backgroundColor: error ? 'rgb(254,243,242)' : 'rgb(236,253,243)',
            border: `1px solid ${error ? 'rgb(253,162,155)' : 'rgb(166,244,197)'}` }}>
          <span className="flex-1">{error ?? notice}</span>
          <button type="button" aria-label="Dismiss" onClick={() => { setError(null); setNotice(null) }}>
            <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth={2} aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
      )}

      <div className="bg-white" style={{ border: `1px solid ${LINE}`, borderRadius: 4 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Staff">
          <thead>
            <tr>
              <th style={{ ...head, width: '22%' }}>Name</th>
              <th style={{ ...head, width: '30%' }}>Email</th>
              <th style={{ ...head, width: '16%' }}>Phone</th>
              <th style={{ ...head, width: '14%' }}>User Type</th>
              <th style={{ ...head, width: '18%' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((row) => (
              <tr key={row.id} data-user-id={row.id}>
                <td style={cell}>{nameCell(row)}</td>
                <td style={cell}>{emailCell(row)}</td>
                <td style={{ ...cell, fontSize: 14, color: TEXT }}>{row.phone_display ?? row.phone ?? ''}</td>
                <td style={cell}>{typeCell(row)}</td>
                <td style={cell}>{actions(row)}</td>
              </tr>
            ))}
            {filtered.data && people.length === 0 && (
              <tr>
                <td colSpan={5} style={{ ...cell, textAlign: 'center', color: MUTED, fontSize: 14 }}>
                  {q.trim() || role ? 'No staff match.' : 'No staff yet.'}
                </td>
              </tr>
            )}
            {filtered.isLoading && (
              <tr><td colSpan={5} style={{ ...cell, color: MUTED, fontSize: 14 }}>Loading…</td></tr>
            )}
          </tbody>
        </table>
        <div className="flex items-center" style={{ padding: '20px 28px', minHeight: 78 }}>
          <span style={{ fontSize: 14, color: TEXT }}>Page {current}</span>
          <div className="ml-auto flex items-center gap-3">
            <button type="button" disabled={current <= 1} onClick={() => setPage(current - 1)}
              style={{ height: 32, padding: '0 12px', borderRadius: 4, border: `1px solid ${LINE}`,
                fontSize: 13, color: current <= 1 ? 'rgb(152,162,179)' : TEXT }}>
              Previous
            </button>
            {Array.from({ length: pages }, (_, i) => i + 1).map((n) => (
              <button key={n} type="button" aria-current={n === current ? 'page' : undefined}
                onClick={() => setPage(n)}
                style={{ minWidth: 24, height: 26, borderRadius: 3, fontSize: 13,
                  border: `1px solid ${n === current ? BLUE : 'transparent'}`,
                  color: n === current ? BLUE : TEXT }}>
                {n}
              </button>
            ))}
            <button type="button" disabled={current >= pages} onClick={() => setPage(current + 1)}
              style={{ height: 32, padding: '0 12px', borderRadius: 4, border: `1px solid ${LINE}`,
                fontSize: 13, color: current >= pages ? 'rgb(152,162,179)' : TEXT }}>
              Next
            </button>
          </div>
        </div>
      </div>

      {machines.length > 0 && (
        <div style={{ marginTop: 28 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>Machine accounts</div>
          <div style={{ fontSize: 13, color: MUTED, marginTop: 2 }}>
            Token-only accounts, such as the telephony feed. They have no password and can never
            sign in; deactivating one stops its token until it is reactivated.
          </div>
          <div className="bg-white" style={{ border: `1px solid ${LINE}`, borderRadius: 4, marginTop: 10 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Machine accounts">
              <tbody>
                {machines.map((row) => (
                  <tr key={row.id} data-user-id={row.id}>
                    <td style={{ ...cell, width: '22%' }}>{nameCell(row)}</td>
                    <td style={{ ...cell, width: '30%' }}>{emailCell(row)}</td>
                    <td style={{ ...cell, width: '16%', fontSize: 13, color: MUTED }}>Machine account</td>
                    <td style={{ ...cell, width: '14%' }}>{typeCell(row)}</td>
                    <td style={{ ...cell, width: '18%' }}>{actions(row)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {confirm && (
        <div className="fixed inset-0 z-40 flex items-center justify-center"
          style={{ backgroundColor: 'rgba(52,64,84,0.6)' }} onClick={() => setConfirm(null)}>
          <div role="alertdialog" aria-modal="true" aria-label={`Deactivate ${confirm.name}`}
            onClick={(e) => e.stopPropagation()} className="bg-white"
            style={{ width: 440, borderRadius: 8, padding: 20,
              boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>Deactivate {confirm.name}?</div>
            <div style={{ fontSize: 14, color: TEXT, marginTop: 8, lineHeight: 1.5 }}>
              {confirm.machine
                ? 'Its API token stops working at once, and works again if you reactivate it.'
                : 'They are signed out everywhere at once and cannot sign in. Their API tokens are revoked. Their name stays on every job, note and task, and you can reactivate them later.'}
            </div>
            <div className="flex justify-end gap-3" style={{ marginTop: 18 }}>
              <button type="button" onClick={() => setConfirm(null)}
                style={{ height: 36, padding: '0 14px', borderRadius: 6, border: `1px solid ${FIELD_LINE}`,
                  fontSize: 14, fontWeight: 600, color: TEXT }}>
                Cancel
              </button>
              <button type="button" disabled={setActive.isPending}
                onClick={() => setActive.mutate({ row: confirm, active: false })}
                style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 14, fontWeight: 600,
                  color: '#fff', backgroundColor: 'rgb(217,45,32)' }}>
                {setActive.isPending ? 'Deactivating…' : 'Deactivate'}
              </button>
            </div>
          </div>
        </div>
      )}

      {dialog && (
        <StaffUserModal
          mode={dialog.kind}
          row={dialog.kind === 'edit' ? dialog.row : null}
          me={user}
          everyone={all}
          onClose={() => setDialog(null)}
          onSaved={(message) => { setDialog(null); setNotice(message); refresh() }}
        />
      )}
    </div>
  )
}
