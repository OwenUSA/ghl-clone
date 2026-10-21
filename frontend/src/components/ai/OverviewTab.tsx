import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  listUsers, registryOverview, registrySetLevel, registrySetOverall, registryUpdateEntry,
  type RegistryAction, type RegistryEntry,
} from '../../lib/api'
import type { Me } from '../../lib/auth'
import { isAiAdmin } from '../../lib/aiAgents'
import {
  groupByRole, isPromotion, LEVEL_HELP, LEVEL_TONE, LEVELS, levelLabel, SETTING_FIELDS, settingValue,
  spendLine, STATUS_TONE, visible, type Show,
} from '../../lib/registry'
import {
  Chip, Empty, FAINT, Field, INK, INPUT, LINE, Modal, MUTED, Notice, PageHeader,
  primarySmall, smallButton, SubTabs, TEXT, textarea,
} from './aiUi'

/**
 * Overview (2026-10-21): every agent and automation in one list, under the boss's ten roles,
 * each with its scope card and the level of each action. Admins change levels, limits, owners
 * and roles; everyone in the module reads it. The server enforces every rule.
 */
export function OverviewTab({ user }: { user: Me }) {
  const admin = isAiAdmin(user)
  const q = useQuery({ queryKey: ['registry'], queryFn: registryOverview, refetchInterval: 30000 })
  const [show, setShow] = useState<Show>('all')
  const [tests, setTests] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const data = q.data
  const entries = data ? visible(data.entries, show, tests) : []
  const groups = data ? groupByRole(entries, data.roles) : []
  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 24px' }}>
      <PageHeader title="Overview"
        subtitle="Every agent and automation, the job it does, and how much freedom each of its actions has." />
      <Notice error={error} notice={notice} onDismiss={() => { setError(null); setNotice(null) }} />
      {data && <Summary overall={data.overall} admin={admin}
        onError={setError} onSaved={() => setNotice('Overall daily limit saved.')} />}
      <Legend />
      <div className="flex flex-wrap items-center gap-12" style={{ marginTop: 16 }}>
        <SubTabs label="Show" active={show} onSelect={setShow} tabs={[
          { key: 'all', label: 'All' }, { key: 'agents', label: 'Agents' },
          { key: 'automations', label: 'Automations' }]} />
        <label className="flex items-center gap-4" style={{ fontSize: 13, color: MUTED }}>
          <input id="registry-show-tests" type="checkbox" checked={tests}
            onChange={(e) => setTests(e.target.checked)} /> Show test agents
        </label>
      </div>
      {q.isLoading && <Empty>Loading…</Empty>}
      {q.isError && <Empty>The overview could not be loaded. {(q.error as Error).message}</Empty>}
      {groups.map((g) => (
        <section key={g.role ?? 'none'} style={{ marginTop: 20 }}>
          <div className="flex items-baseline gap-8">
            {g.role != null && <span style={{ fontSize: 12, fontWeight: 600, color: FAINT,
              fontVariantNumeric: 'tabular-nums' }}>{g.role}</span>}
            <h2 style={{ fontSize: 15, fontWeight: 600, color: INK }}>{g.name}</h2>
          </div>
          {g.entries.length === 0 ? (
            <div style={{ fontSize: 13, color: FAINT, marginTop: 6 }}>Nothing does this job yet.</div>
          ) : (
            <div className="bg-white" style={{ marginTop: 8, border: `1px solid ${LINE}`, borderRadius: 4 }}>
              {g.entries.map((e, i) => (
                <EntryRow key={e.key} e={e} first={i === 0} admin={admin}
                  open={open === e.key} onToggle={() => setOpen(open === e.key ? null : e.key)}
                  roles={data!.roles} promoteWord={data!.promote_confirm}
                  onError={setError} onNotice={setNotice} />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  )
}

function Summary({ overall, admin, onError, onSaved }: {
  overall: { spend_today: string | null; spend_unknown: boolean; daily_cap: string | null; paused: boolean }
  admin: boolean; onError: (s: string) => void; onSaved: () => void
}) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [cap, setCap] = useState('')
  const save = useMutation({
    mutationFn: () => registrySetOverall(cap),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ['registry'] }); onSaved() },
    onError: (e: Error) => onError(e.message),
  })
  return (
    <div className="flex flex-wrap items-center gap-16 bg-white" style={{ marginTop: 12,
      border: `1px solid ${LINE}`, borderRadius: 4, padding: '10px 12px', fontSize: 13, color: TEXT }}>
      <span>
        <span style={{ color: MUTED }}>All agents together: </span>
        <strong style={{ color: INK, fontVariantNumeric: 'tabular-nums' }}>
          {spendLine(overall.spend_today, overall.daily_cap, false)}
        </strong>
        {overall.spend_unknown && <span style={{ color: FAINT }}> (some spend unknown)</span>}
      </span>
      {overall.paused && <Chip text="All AI agents are paused" fg="rgb(180,35,24)" bg="rgb(254,243,242)" />}
      {admin && !editing && (
        <button type="button" style={smallButton}
          onClick={() => { setCap((overall.daily_cap ?? '').replace('$', '')); setEditing(true) }}>
          Change the overall limit
        </button>
      )}
      {admin && editing && (
        <span className="flex items-center gap-8">
          <label htmlFor="registry-overall-cap" style={{ color: MUTED }}>Daily limit, $</label>
          <input id="registry-overall-cap" value={cap} onChange={(e) => setCap(e.target.value)}
            inputMode="decimal" style={{ ...INPUT, marginTop: 0, width: 90, height: 30 }} />
          <button type="button" style={primarySmall} disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
          <button type="button" style={smallButton} onClick={() => setEditing(false)}>Cancel</button>
        </span>
      )}
      <span style={{ color: FAINT, marginLeft: 'auto' }}>Voice minutes are counted by the phone system: see Phone numbers.</span>
    </div>
  )
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-16" style={{ marginTop: 12, fontSize: 12, color: MUTED }}>
      {LEVELS.map((l) => (
        <span key={l} className="flex items-center gap-6">
          <Chip text={levelLabel(l)} fg={LEVEL_TONE[l].fg} bg={LEVEL_TONE[l].bg} />
          {LEVEL_HELP[l]}
        </span>
      ))}
    </div>
  )
}

function EntryRow({ e, first, admin, open, onToggle, roles, promoteWord, onError, onNotice }: {
  e: RegistryEntry; first: boolean; admin: boolean; open: boolean; onToggle: () => void
  roles: { role: number; name: string }[]; promoteWord: string
  onError: (s: string) => void; onNotice: (s: string) => void
}) {
  const st = STATUS_TONE[e.status.state] ?? STATUS_TONE.unknown
  return (
    <div style={{ borderTop: first ? 'none' : `1px solid ${LINE}` }}>
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-8 text-left"
        style={{ padding: '10px 12px' }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: INK, minWidth: 180 }}>{e.name}</span>
        <Chip text={st.label} fg={st.fg} bg={st.bg} title={e.status.sentence} />
        {e.kind === 'automation' && <Chip text="Automation" fg="rgb(71,84,103)" bg="rgb(242,244,247)" />}
        {e.is_test && <Chip text="Test" fg="rgb(181,71,8)" bg="rgb(255,250,235)" />}
        {e.health.error_today && <Chip text="Failed today" fg="rgb(180,35,24)" bg="rgb(254,243,242)"
          title={e.health.last_error ?? undefined} />}
        <span className="flex flex-wrap gap-4" style={{ marginLeft: 'auto' }}>
          {e.actions.map((a) => (
            <Chip key={a.action} text={`${a.label}: ${levelLabel(a.level)}`}
              fg={LEVEL_TONE[a.level]?.fg ?? INK} bg={LEVEL_TONE[a.level]?.bg ?? '#fff'} />
          ))}
        </span>
      </button>
      {open && <ScopeCard e={e} admin={admin} roles={roles} promoteWord={promoteWord}
        onError={onError} onNotice={onNotice} />}
    </div>
  )
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap" style={{ gap: '2px 12px', fontSize: 13, lineHeight: 1.5 }}>
      <span style={{ width: 120, flexShrink: 0, color: MUTED }}>{label}</span>
      <span style={{ flex: '1 1 260px', color: INK }}>{children}</span>
    </div>
  )
}

function ScopeCard({ e, admin, roles, promoteWord, onError, onNotice }: {
  e: RegistryEntry; admin: boolean; roles: { role: number; name: string }[]; promoteWord: string
  onError: (s: string) => void; onNotice: (s: string) => void
}) {
  const [editing, setEditing] = useState(false)
  return (
    <div style={{ padding: '4px 12px 14px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Line label="Purpose">{e.purpose}</Line>
      <Line label="Covers">{e.covers}</Line>
      <Line label="Channels">{e.channels.join(' · ') || '—'}</Line>
      <Line label="Runs on">{e.runs_on}{e.provider ? ` · ${e.provider}` : ''}</Line>
      <Line label="Hours">{e.hours}</Line>
      <Line label="Status">{e.status.sentence}</Line>
      <Line label="Answers for it">{e.owner ?? 'Nobody named yet'}</Line>
      <Line label="Spend">{spendLine(e.spend_today, e.daily_cap, e.spend_unknown)}</Line>
      {e.never.length > 0 && (
        <Line label="Never">
          <ul style={{ margin: 0, paddingLeft: 18 }}>{e.never.map((n) => <li key={n}>{n}</li>)}</ul>
        </Line>
      )}
      {e.health.last_error && (
        <Line label="Last error">
          <span style={{ color: 'rgb(180,35,24)' }}>{e.health.last_error}</span>
          {e.health.last_error_at && <span style={{ color: FAINT }}> · {new Date(e.health.last_error_at).toLocaleString()}</span>}
        </Line>
      )}
      {e.notes && <Line label="Notes">{e.notes}</Line>}
      <div style={{ marginTop: 8, fontSize: 13, fontWeight: 600, color: INK }}>Actions</div>
      <div style={{ border: `1px solid ${LINE}`, borderRadius: 4 }}>
        {e.actions.map((a, i) => (
          <ActionRow key={a.action} entry={e} a={a} first={i === 0} admin={admin}
            promoteWord={promoteWord} onError={onError} onNotice={onNotice} />
        ))}
      </div>
      {e.settings && <SettingsPanel e={e} admin={admin} onError={onError} onNotice={onNotice} />}
      {admin && !editing && (
        <div><button type="button" style={{ ...smallButton, marginTop: 8 }} onClick={() => setEditing(true)}>
          Edit role, owner and limit
        </button></div>
      )}
      {admin && editing && <EditEntry e={e} roles={roles} onDone={(msg) => { setEditing(false); if (msg) onNotice(msg) }}
        onError={onError} />}
    </div>
  )
}

function ActionRow({ entry, a, first, admin, promoteWord, onError, onNotice }: {
  entry: RegistryEntry; a: RegistryAction; first: boolean; admin: boolean; promoteWord: string
  onError: (s: string) => void; onNotice: (s: string) => void
}) {
  const qc = useQueryClient()
  const [pending, setPending] = useState<string | null>(null)
  const set = useMutation({
    mutationFn: (body: { level: string; reason?: string; confirm?: string }) =>
      registrySetLevel(entry.key, a.action, body),
    onSuccess: (_d, body) => {
      setPending(null)
      qc.invalidateQueries({ queryKey: ['registry'] })
      onNotice(`${entry.name}: “${a.label}” is now ${levelLabel(body.level)}.`)
    },
    onError: (err: Error) => onError(err.message),
  })
  const editable = admin && a.choices.length > 0
  return (
    <div className="flex flex-wrap items-center gap-8" style={{ padding: '8px 10px',
      borderTop: first ? 'none' : `1px solid ${LINE}`, fontSize: 13 }}>
      <span style={{ flex: '1 1 220px', color: INK }}>
        {a.label}
        {a.customer && <span style={{ color: FAINT }}> · customers see this</span>}
        {a.note && <span className="block" style={{ fontSize: 12, color: FAINT, marginTop: 2 }}>{a.note}</span>}
      </span>
      {editable ? (
        <select aria-label={`${a.label} level`} value={a.level} disabled={set.isPending}
          onChange={(ev) => {
            const to = ev.target.value
            if (isPromotion(a.level, to)) setPending(to)
            else set.mutate({ level: to })
          }}
          style={{ ...INPUT, marginTop: 0, height: 30, width: 'auto', fontSize: 13 }}>
          {a.choices.map((c) => <option key={c} value={c}>{levelLabel(c)}</option>)}
        </select>
      ) : (
        <Chip text={levelLabel(a.level)} fg={LEVEL_TONE[a.level]?.fg ?? INK} bg={LEVEL_TONE[a.level]?.bg ?? '#fff'}
          title={a.choices.length ? undefined : 'Not set here'} />
      )}
      {pending && <PromoteDialog entry={entry} a={a} to={pending} word={promoteWord} busy={set.isPending}
        onCancel={() => setPending(null)}
        onConfirm={(reason, confirm) => set.mutate({ level: pending, reason, confirm })} />}
    </div>
  )
}

function PromoteDialog({ entry, a, to, word, busy, onCancel, onConfirm }: {
  entry: RegistryEntry; a: RegistryAction; to: string; word: string; busy: boolean
  onCancel: () => void; onConfirm: (reason: string, confirm: string) => void
}) {
  const [reason, setReason] = useState('')
  const [typed, setTyped] = useState('')
  const ready = reason.trim() !== '' && typed.trim() === word
  return (
    <Modal title={`Give ${entry.name} more freedom?`} onClose={onCancel}
      subtitle={`“${a.label}” goes from ${levelLabel(a.level)} to ${levelLabel(to)}.`}
      footer={<>
        <button type="button" style={smallButton} onClick={onCancel}>Cancel</button>
        <button type="button" style={{ ...primarySmall, opacity: ready ? 1 : 0.5 }} disabled={!ready || busy}
          onClick={() => onConfirm(reason.trim(), typed.trim())}>Promote</button>
      </>}>
      <div style={{ fontSize: 13, color: TEXT, lineHeight: 1.5 }}>
        The rule we agreed: promote only after two weeks or 30 actions, whichever is later, with no
        mistakes. The reason is kept with the change in Activity.
      </div>
      <Field label="Why now" required>
        <textarea id="registry-promote-reason" value={reason} onChange={(e) => setReason(e.target.value)}
          style={textarea} placeholder="Two weeks in shadow, 34 replies checked, no mistakes." />
      </Field>
      <Field label={`Type ${word} to confirm`} required>
        <input id="registry-promote-word" value={typed} onChange={(e) => setTyped(e.target.value)} style={INPUT} />
      </Field>
    </Modal>
  )
}

function EditEntry({ e, roles, onDone, onError }: {
  e: RegistryEntry; roles: { role: number; name: string }[]
  onDone: (msg?: string) => void; onError: (s: string) => void
}) {
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const [role, setRole] = useState(e.role == null ? '' : String(e.role))
  const [owner, setOwner] = useState(e.owner_user_id == null ? '' : String(e.owner_user_id))
  const [cap, setCap] = useState((e.daily_cap ?? '').replace('$', ''))
  const [isTest, setIsTest] = useState(e.is_test)
  const [notes, setNotes] = useState(e.notes ?? '')
  const save = useMutation({
    mutationFn: () => registryUpdateEntry(e.key, {
      role: role === '' ? null : Number(role), owner_user_id: owner === '' ? null : Number(owner),
      daily_cap_usd: cap.trim() === '' ? null : cap.trim(), is_test: isTest, notes }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['registry'] }); onDone(`${e.name} saved.`) },
    onError: (err: Error) => onError(err.message),
  })
  const sel: React.CSSProperties = { ...INPUT, marginTop: 4 }
  return (
    <div style={{ marginTop: 8, padding: 12, border: `1px solid ${LINE}`, borderRadius: 4 }}>
      <div className="grid gap-12" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
        <Field label="Role">
          <select id={`registry-role-${e.key}`} value={role} onChange={(ev) => setRole(ev.target.value)} style={sel}>
            <option value="">Not assigned</option>
            {roles.map((r) => <option key={r.role} value={r.role}>{r.role}. {r.name}</option>)}
          </select>
        </Field>
        <Field label="Answers for it">
          <select id={`registry-owner-${e.key}`} value={owner} onChange={(ev) => setOwner(ev.target.value)} style={sel}>
            <option value="">Nobody named</option>
            {(users.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </Field>
        <Field label="Its daily limit, $" hint="Empty = only the overall limit applies.">
          <input id={`registry-cap-${e.key}`} value={cap} onChange={(ev) => setCap(ev.target.value)}
            inputMode="decimal" style={sel} />
        </Field>
      </div>
      <label className="flex items-center gap-6" style={{ marginTop: 12, fontSize: 13, color: TEXT }}>
        <input id={`registry-test-${e.key}`} type="checkbox" checked={isTest} onChange={(ev) => setIsTest(ev.target.checked)} />
        A test agent (hidden from the overview unless “Show test agents” is ticked)
      </label>
      <Field label="Notes">
        <textarea id={`registry-notes-${e.key}`} value={notes} onChange={(ev) => setNotes(ev.target.value)} style={textarea} />
      </Field>
      <div className="flex gap-8" style={{ marginTop: 12 }}>
        <button type="button" style={primarySmall} disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
        <button type="button" style={smallButton} onClick={() => onDone()}>Cancel</button>
      </div>
      <div style={{ fontSize: 12, color: FAINT, marginTop: 8 }}>
        To rename an agent or switch it on, open it in Agents.
      </div>
    </div>
  )
}

function SettingsPanel({ e, admin, onError, onNotice }: {
  e: RegistryEntry; admin: boolean; onError: (s: string) => void; onNotice: (s: string) => void
}) {
  const qc = useQueryClient()
  const current = e.settings ?? {}
  const shown = (_k: string, v: unknown) => Array.isArray(v) ? v.join(', ') : typeof v === 'boolean' ? v : String(v ?? '')
  const [draft, setDraft] = useState<Record<string, string | boolean>>(
    Object.fromEntries(Object.entries(current).map(([k, v]) => [k, shown(k, v)])))
  const save = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(draft)) {
        const f = SETTING_FIELDS[k]
        if (f && shown(k, current[k]) !== v) config[k] = settingValue(f.kind, v)
      }
      return registryUpdateEntry(e.key, { config })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['registry'] }); onNotice(`${e.name}: settings saved.`) },
    onError: (err: Error) => onError(err.message),
  })
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>Settings</div>
      <div style={{ border: `1px solid ${LINE}`, borderRadius: 4, padding: '4px 12px 12px', marginTop: 6 }}>
        {Object.keys(current).map((k) => {
          const f = SETTING_FIELDS[k]
          const id = `registry-setting-${e.key}-${k}`
          if (!f) return <Line key={k} label={k}>{String(shown(k, current[k]))}</Line>
          if (f.kind === 'bool') {
            return (
              <label key={k} htmlFor={id} className="flex items-center gap-6" style={{ marginTop: 12, fontSize: 13, color: TEXT }}>
                <input id={id} type="checkbox" disabled={!admin} checked={!!draft[k]}
                  onChange={(ev) => setDraft({ ...draft, [k]: ev.target.checked })} />
                {f.label}{f.hint && <span style={{ color: FAINT }}> - {f.hint}</span>}
              </label>
            )
          }
          return (
            <Field key={k} label={f.label} hint={f.hint}>
              {f.kind === 'long' ? (
                <textarea id={id} disabled={!admin} value={String(draft[k] ?? '')}
                  onChange={(ev) => setDraft({ ...draft, [k]: ev.target.value })} style={textarea} />
              ) : (
                <input id={id} disabled={!admin} value={String(draft[k] ?? '')}
                  inputMode={f.kind === 'int' ? 'numeric' : undefined}
                  onChange={(ev) => setDraft({ ...draft, [k]: ev.target.value })} style={INPUT} />
              )}
            </Field>
          )
        })}
        {admin && (
          <button type="button" style={{ ...primarySmall, marginTop: 12 }} disabled={save.isPending}
            onClick={() => save.mutate()}>Save settings</button>
        )}
      </div>
    </div>
  )
}
