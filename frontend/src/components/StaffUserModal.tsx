import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import {
  createStaffUser, patchStaffUser, type StaffPatch, type StaffUser,
} from '../lib/api'
import type { Me } from '../lib/auth'
import {
  ROLE_LABELS, defaultOnlyAssigned, joinName, roleBlock, splitName,
} from '../lib/access'
import { IconClose } from './PipelineIcons'
import { Chevron } from './opportunity/ui'

const INK = 'rgb(16,24,40)'
const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(208,213,221)'
const SOFT_LINE = 'rgb(234,236,240)'
const BLUE = 'rgb(21,94,239)'
const DANGER = 'rgb(217,45,32)'
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

/**
 * Add User / Edit User — in the Create pipeline modal's style (refs/opps/04), because no
 * GoHighLevel screenshot of its own user dialog exists. GoHighLevel's has per-module
 * permission tabs; the owner ruled those out (Q4): role + "Only assigned data" is all.
 *
 *   First name *  | Last name
 *   Email *       | Phone
 *   Role *
 *   [Only assigned data ◯]   ON by default for a Technician, still toggleable
 *   Password *     (Add)  — "Reset password" (Edit), both forcing a change at sign-in
 */
export function StaffUserModal({ mode, row, me, everyone, onClose, onSaved }: {
  mode: 'add' | 'edit'
  row: StaffUser | null
  me: Me
  everyone: StaffUser[]
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const start = row ? splitName(row.name) : { first: '', last: '' }
  const [first, setFirst] = useState(start.first)
  const [last, setLast] = useState(start.last)
  const [email, setEmail] = useState(row?.email ?? '')
  const [phone, setPhone] = useState(row?.phone ?? '')
  const [role, setRole] = useState<string>(row?.role ?? 'TECH')
  const [restricted, setRestricted] = useState(row ? row.only_assigned_data : defaultOnlyAssigned('TECH'))
  // Until the admin touches the switch, a role change moves it to that role's default.
  const [switchTouched, setSwitchTouched] = useState(mode === 'edit')
  const [password, setPassword] = useState('')
  const [resetting, setResetting] = useState(false)
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const lockRole = row ? roleBlock(row, me.id, everyone) : null
  const adminRole = role === 'ADMIN'

  const problems: Record<string, string | undefined> = {
    first: first.trim() ? undefined : 'First name is required',
    email: EMAIL.test(email.trim()) ? undefined : 'Enter a valid email address',
    password: mode === 'add' || resetting
      ? (password.length >= 8 ? undefined : 'At least 8 characters') : undefined,
  }
  const ok = !Object.values(problems).some(Boolean)

  const save = useMutation({
    mutationFn: async () => {
      const name = joinName(first, last)
      const only = adminRole ? false : restricted
      if (mode === 'add') {
        const made = await createStaffUser({ name, email: email.trim(), phone: phone.trim() || null,
          role, password, only_assigned_data: only })
        const cal = made.calendar
        return `${made.name} was added. They choose their own password when they first sign in.`
          + (cal ? ` Their calendar “${cal.name}” was created`
            + (cal.renamed_from ? ` (a calendar called “${cal.renamed_from}” already existed and was left alone).` : '.')
            : '')
      }
      const body: StaffPatch = {}
      if (name !== row!.name) body.name = name
      if (email.trim().toLowerCase() !== row!.email) body.email = email.trim()
      if ((phone.trim() || null) !== (row!.phone ?? null)) body.phone = phone.trim() || null
      if (role !== row!.role) body.role = role
      if (only !== row!.only_assigned_data) body.only_assigned_data = only
      if (resetting) body.password = password
      if (Object.keys(body).length) await patchStaffUser(row!.id, body)
      return resetting
        ? `${name}'s password was reset. They must choose a new one when they next sign in.`
        : `${name} was updated.`
    },
    onSuccess: onSaved,
    onError: (e: Error) => setError(e.message),
  })

  const input = (invalid: boolean): React.CSSProperties => ({
    width: '100%', height: 36, marginTop: 6, borderRadius: 6, padding: '0 12px', fontSize: 14,
    color: INK, outline: 'none', backgroundColor: '#fff',
    border: `1px solid ${invalid ? 'rgb(253,162,155)' : LINE}`,
    boxShadow: '0 1px 2px rgba(16,24,40,0.05)',
  })
  const label: React.CSSProperties = { fontSize: 13, fontWeight: 500, color: TEXT }
  const star = <span style={{ color: DANGER }}> *</span>
  const hint = (key: string) => touched && problems[key]
    ? <div style={{ fontSize: 12, color: DANGER, marginTop: 4 }}>{problems[key]}</div> : null

  const title = mode === 'add' ? 'Add User' : 'Edit User'

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(52,64,84,0.6)' }} onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        className="flex flex-col bg-white"
        style={{ width: 600, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100vh - 32px)',
          borderRadius: 8, boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}>
        <div className="flex items-center" style={{ padding: '18px 16px 4px' }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>{title}</div>
          <button type="button" onClick={onClose} aria-label="Close" className="ml-auto" style={{ padding: 4 }}>
            <IconClose size={18} color={TEXT} />
          </button>
        </div>
        <div style={{ fontSize: 13, color: MUTED, padding: '0 16px 12px' }}>
          {mode === 'add'
            ? 'They sign in with the password you set here, then choose their own.'
            : 'Changes to the role and “Only assigned data” apply on their next click.'}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto" style={{ padding: '4px 16px 16px' }}>
          <div className="grid grid-cols-2" style={{ columnGap: 12, rowGap: 14 }}>
            <div>
              <label htmlFor="staff-first" style={label}>First name{star}</label>
              <input id="staff-first" autoFocus value={first} maxLength={60}
                onChange={(e) => setFirst(e.target.value)} placeholder="First name"
                style={input(touched && !!problems.first)} />
              {hint('first')}
            </div>
            <div>
              <label htmlFor="staff-last" style={label}>Last name</label>
              <input id="staff-last" value={last} maxLength={60}
                onChange={(e) => setLast(e.target.value)} placeholder="Last name" style={input(false)} />
            </div>
            <div>
              <label htmlFor="staff-email" style={label}>Email{star}</label>
              <input id="staff-email" type="email" value={email} maxLength={255} autoComplete="off"
                onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com"
                style={input(touched && !!problems.email)} />
              {hint('email')}
            </div>
            <div>
              <label htmlFor="staff-phone" style={label}>Phone</label>
              <input id="staff-phone" type="tel" value={phone} maxLength={40}
                onChange={(e) => setPhone(e.target.value)} placeholder="(941) 555-0100" style={input(false)} />
            </div>
          </div>

          <div style={{ marginTop: 14 }}>
            <label htmlFor="staff-role" style={label}>Role{star}</label>
            <div className="relative">
              <select id="staff-role" value={role} disabled={!!lockRole} title={lockRole ?? undefined}
                onChange={(e) => {
                  setRole(e.target.value)
                  if (!switchTouched) setRestricted(defaultOnlyAssigned(e.target.value))
                }}
                style={{ ...input(false), appearance: 'none', paddingRight: 34,
                  ...(lockRole ? { backgroundColor: 'rgb(249,250,251)', cursor: 'not-allowed' } : {}) }}>
                {Object.entries(ROLE_LABELS).map(([key, text]) => (
                  <option key={key} value={key}>{text}</option>
                ))}
              </select>
              <span className="pointer-events-none absolute" style={{ right: 12, top: 16 }}><Chevron /></span>
            </div>
            {lockRole && <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>{lockRole}</div>}
            {mode === 'add' && role === 'TECH' && (
              <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
                A technician gets their own calendar, so visits can be booked on it.
              </div>
            )}
          </div>

          <div className="flex items-center" style={{ marginTop: 14, border: `1px solid ${SOFT_LINE}`,
            borderRadius: 8, padding: '14px 16px' }}>
            <div className="flex-1">
              <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>Only assigned data</div>
              <div style={{ fontSize: 12, color: MUTED, marginTop: 2, lineHeight: 1.5 }}>
                {adminRole
                  ? 'An admin always sees everything.'
                  : 'Sees only the jobs they own or have a visit on, and those customers, conversations, calendar entries, tasks, notes and photos. No Dashboard, Reporting or Forecast.'}
              </div>
            </div>
            <button type="button" role="switch" aria-checked={!adminRole && restricted}
              aria-label="Only assigned data" disabled={adminRole}
              title={adminRole ? 'An admin always sees everything' : undefined}
              onClick={() => { setRestricted((v) => !v); setSwitchTouched(true) }}
              style={{ width: 36, height: 20, borderRadius: 10, position: 'relative', flexShrink: 0,
                marginLeft: 16, backgroundColor: !adminRole && restricted ? BLUE : SOFT_LINE,
                transition: 'background-color 120ms', opacity: adminRole ? 0.5 : 1,
                cursor: adminRole ? 'not-allowed' : 'pointer' }}>
              <span style={{ position: 'absolute', top: 2, width: 16, height: 16, borderRadius: 8,
                left: !adminRole && restricted ? 18 : 2, backgroundColor: '#fff',
                boxShadow: '0 1px 3px rgba(16,24,40,0.1)', transition: 'left 120ms' }} />
            </button>
          </div>

          {mode === 'add' ? (
            <div style={{ marginTop: 14 }}>
              <label htmlFor="staff-password" style={label}>Password{star}</label>
              <input id="staff-password" type="password" value={password} autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters"
                style={input(touched && !!problems.password)} />
              {hint('password') ?? (
                <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
                  Give it to them yourself — no invitation email is sent.
                </div>
              )}
            </div>
          ) : (
            <div style={{ marginTop: 14 }}>
              {resetting ? (
                <>
                  <label htmlFor="staff-password" style={label}>New password{star}</label>
                  <input id="staff-password" type="password" value={password} autoFocus
                    autoComplete="new-password" onChange={(e) => setPassword(e.target.value)}
                    placeholder="At least 8 characters" style={input(touched && !!problems.password)} />
                  {hint('password') ?? (
                    <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
                      Signs them out everywhere; they must choose their own at the next sign-in.
                    </div>
                  )}
                  <button type="button" onClick={() => { setResetting(false); setPassword('') }}
                    style={{ fontSize: 13, color: MUTED, marginTop: 6 }}>
                    Keep their current password
                  </button>
                </>
              ) : (
                <button type="button" onClick={() => setResetting(true)}
                  style={{ fontSize: 14, fontWeight: 500, color: BLUE }}>
                  Reset password
                </button>
              )}
            </div>
          )}

          {error && (
            <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)',
              backgroundColor: 'rgb(254,243,242)', border: '1px solid rgb(253,162,155)',
              borderRadius: 8, padding: '8px 12px' }}>
              {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3" style={{ padding: '14px 16px', borderTop: `1px solid ${SOFT_LINE}` }}>
          <button type="button" onClick={onClose} style={{ height: 36, padding: '0 14px', borderRadius: 6,
            border: `1px solid ${LINE}`, fontSize: 14, fontWeight: 600, color: TEXT, backgroundColor: '#fff' }}>
            Cancel
          </button>
          <button type="button"
            onClick={() => { setError(null); setTouched(true); if (ok) save.mutate() }}
            disabled={save.isPending}
            title={ok ? undefined : Object.values(problems).find(Boolean)}
            style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 14, fontWeight: 600,
              color: '#fff', backgroundColor: (ok || !touched) && !save.isPending ? BLUE : 'rgb(178,204,255)',
              cursor: save.isPending ? 'not-allowed' : 'pointer' }}>
            {save.isPending ? (mode === 'add' ? 'Adding…' : 'Updating…') : mode === 'add' ? 'Add User' : 'Update'}
          </button>
        </div>
      </div>
    </div>
  )
}
