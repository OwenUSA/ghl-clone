import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { PageTabs } from '../components/PageTabs'
import { BLUE, INK, LINE, PAGE_BG } from '../components/ai/aiUi'
import {
  followupAction, followupGet, followupSaveSequence, followupSequences, followupsList,
  followupsStaff, followupsStatus,
  type FollowupDetail, type FollowupSequence, type FollowupStep, type FollowupStepType,
} from '../lib/api'
import type { Me } from '../lib/auth'
import { canOpenAiAgents } from '../lib/aiAgents'
import { EVENT_LABEL, SHOWS, chipFor, eventTone, relative, when } from '../lib/followups'
import { openRecord, canOpenRecords } from '../lib/openRecord'
import { NewFollowupForm } from '../components/FollowUpButton'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`, borderRadius: 8,
  padding: '12px 14px' }
const btn: React.CSSProperties = { height: 28, padding: '0 10px', borderRadius: 4, fontSize: 13,
  fontWeight: 500, border: `1px solid ${LINE}`, background: '#fff', color: INK }
const primary: React.CSSProperties = { ...btn, background: BLUE, borderColor: BLUE, color: '#fff' }
const input: React.CSSProperties = { border: `1px solid ${LINE}`, borderRadius: 4, padding: '5px 8px',
  fontSize: 13, color: INK, fontFamily: 'inherit', width: '100%' }

function Chip({ r }: { r: { state: string; due: boolean; overdue: boolean } }) {
  const t = chipFor(r)
  return <span style={{ fontSize: 12, fontWeight: 600, color: t.fg, background: t.bg, borderRadius: 10,
    padding: '1px 8px', whiteSpace: 'nowrap' }}>{t.label}</span>
}

/**
 * Follow-ups (2026-11-11, app/cadences): customers who did not answer, followed up the way Hatch
 * does - a sequence of texts, "call me" reminders and AI calls that stops by itself when the
 * customer texts or calls back. "Call now" lists the calls a person has to make. Texts and AI calls
 * leave only from the server's pass, at the levels set in AI Agents -> Overview.
 */
export function FollowupsPage({ user, focus }: { user: Me; focus?: { id: number; n: number } | null }) {
  const allowed = canOpenAiAgents(user)
  const [show, setShow] = useState('due')
  const [mine, setMine] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  useEffect(() => { if (focus?.id) { setSelected(focus.id); setShow('open') } }, [focus])
  const list = useQuery({ queryKey: ['followups', show, mine], queryFn: () => followupsList(show, mine),
    enabled: allowed && show !== 'sequences', refetchInterval: 30000 })
  const status = useQuery({ queryKey: ['followups-status'], queryFn: followupsStatus, enabled: allowed,
    refetchInterval: 60000 })
  if (!allowed) return null
  const st = status.data
  return (
    <div className="flex min-w-0 flex-1 flex-col" style={{ height: '100vh', overflow: 'hidden', backgroundColor: PAGE_BG }}>
      <PageTabs title="Follow-ups" label="Follow-ups" active={show}
        tabs={[...SHOWS, { key: 'sequences', label: 'Sequences' }].map((f) => ({ key: f.key, label: f.label,
          onSelect: () => { setShow(f.key); setCreating(false) } }))} />
      <div style={{ padding: '8px 20px', fontSize: 13, color: MUTED, display: 'flex', gap: 18, flexWrap: 'wrap',
        alignItems: 'center', borderBottom: `1px solid ${LINE}`, background: '#fff' }}>
        {st && <>
          <span style={{ color: st.running ? INK : RED }}>{st.sentence}</span>
          <span>Texts: <strong style={{ color: INK }}>{st.levels.send_text}</strong> · AI calls: <strong style={{ color: INK }}>{st.levels.place_calls}</strong></span>
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>Today {st.texts_today}/{st.texts_per_day} texts · {st.ai_calls_today}/{st.ai_calls_per_day} AI calls</span>
        </>}
        {show !== 'sequences' && (
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', marginLeft: 'auto' }}>
            <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine
          </label>
        )}
        <button type="button" style={{ ...primary, marginLeft: show === 'sequences' ? 'auto' : 0 }}
          onClick={() => { setCreating(true); if (show === 'sequences') setShow('open') }}>New follow-up</button>
      </div>
      {show === 'sequences' ? <Sequences admin={!!st?.is_admin} /> : (
        <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(280px, 380px) minmax(0, 1fr)' }}>
          <div style={{ overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 6, borderRight: `1px solid ${LINE}` }}>
            {list.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>}
            {list.error && <div role="alert" style={{ color: RED, fontSize: 13 }}>{(list.error as Error).message}</div>}
            {!list.isLoading && !list.data?.followups.length && <div style={{ color: MUTED, fontSize: 13 }}>
              {show === 'due' ? 'No calls to make right now.' : 'Nothing here.'}</div>}
            {list.data?.followups.map((r) => (
              <button key={r.id} type="button" onClick={() => { setSelected(r.id); setCreating(false) }}
                style={{ ...card, textAlign: 'left', borderColor: selected === r.id ? BLUE : LINE }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span style={{ fontWeight: 600, color: INK, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.customer_name ?? r.phone_display}</span>
                  {r.job_number && <span style={{ fontSize: 12, color: MUTED }}>#{r.job_number}</span>}
                  <span style={{ marginLeft: 'auto' }}><Chip r={r} /></span>
                </div>
                <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
                  {r.state === 'open' && r.next_step
                    ? <>Step {r.step_index + 1}/{r.step_count}: {r.next_step} · {when(r.next_at)} <span style={{ color: r.overdue ? RED : MUTED }}>({relative(r.next_at)})</span></>
                    : r.stop_reason ?? (r.state === 'open' ? 'Waiting for a reply' : '')}
                </div>
                <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
                  {r.assigned_label ? `Assigned to ${r.assigned_label}` : 'Not assigned'}{r.board ? ` · ${r.board}` : ''}
                </div>
              </button>
            ))}
          </div>
          <div style={{ overflowY: 'auto', padding: 16 }}>
            {creating ? <div style={{ ...card, maxWidth: 560 }}>
              <NewFollowupForm onDone={(id) => { setCreating(false); if (id) { setSelected(id); setShow('open') } }} />
            </div>
              : selected != null ? <Detail id={selected} />
                : <div style={{ color: MUTED, fontSize: 13 }}>Pick a customer on the left, or start a new follow-up.</div>}
          </div>
        </div>
      )}
    </div>
  )
}

function useRefresh() {
  const qc = useQueryClient()
  return (d?: FollowupDetail) => {
    qc.invalidateQueries({ queryKey: ['followups'] })
    qc.invalidateQueries({ queryKey: ['followups-summary'] })
    if (d) qc.setQueryData(['followup', d.id], d)
  }
}

function Detail({ id }: { id: number }) {
  const refresh = useRefresh()
  const q = useQuery({ queryKey: ['followup', id], queryFn: () => followupGet(id), refetchInterval: 30000 })
  const staff = useQuery({ queryKey: ['followups-staff'], queryFn: followupsStaff, staleTime: 300000 })
  const [reason, setReason] = useState('')
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = useMutation({
    mutationFn: (body: Parameters<typeof followupAction>[1]) => followupAction(id, body),
    onSuccess: (d) => { setError(null); setStopping(false); setReason(''); refresh(d) },
    onError: (e: Error) => setError(e.message),
  })
  if (q.isLoading) return <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>
  if (q.error || !q.data) return <div role="alert" style={{ color: RED, fontSize: 13 }}>{(q.error as Error)?.message ?? 'Not found.'}</div>
  const d = q.data
  const isOpen = d.state === 'open' || d.state === 'paused'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 760 }}>
      <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 17, fontWeight: 600, color: INK }}>{d.customer_name ?? d.phone_display}</span>
          <span style={{ color: MUTED, fontSize: 13 }}>{d.phone_display}</span>
          {d.job_number && <span style={{ color: MUTED, fontSize: 13 }}>Job #{d.job_number}</span>}
          {d.board && <span style={{ color: MUTED, fontSize: 13 }}>{d.board}</span>}
          <span style={{ marginLeft: 'auto' }}><Chip r={d} /></span>
        </div>
        <div style={{ fontSize: 13, color: MUTED }}>
          {d.sequence_name} · started {when(d.started_at)} {d.source === 'auto' ? 'after an unanswered call' : `by ${d.created_by ?? 'a person'}`}
          {d.stop_reason && <> · <strong style={{ color: INK }}>{d.stop_reason}</strong></>}
        </div>
        {d.note && <div style={{ fontSize: 13, color: INK, whiteSpace: 'pre-wrap' }}>Note: {d.note}</div>}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 4 }}>
          <label htmlFor={`fu-assign-${d.id}`} style={{ fontSize: 13, color: MUTED }}>Assigned to</label>
          <select id={`fu-assign-${d.id}`} style={{ ...input, width: 200 }} value={d.assigned_user_id ?? ''}
            onChange={(e) => act.mutate({ action: 'reassign', user_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">Nobody (whole office)</option>
            {d.assigned_user_id != null && !staff.data?.users.some((u) => u.id === d.assigned_user_id) &&
              <option value={d.assigned_user_id}>{d.assigned_label ?? 'Someone'}</option>}
            {staff.data?.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          {d.contact_id != null && canOpenRecords() &&
            <button type="button" style={btn} onClick={() => openRecord('contacts', d.contact_id!)}>Open contact</button>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
          {d.state === 'open' && <button type="button" style={btn} disabled={act.isPending} onClick={() => act.mutate({ action: 'pause' })}>Pause</button>}
          {d.state === 'paused' && <button type="button" style={btn} disabled={act.isPending} onClick={() => act.mutate({ action: 'resume' })}>Resume</button>}
          {isOpen && d.step_index < d.step_count && <button type="button" style={btn} disabled={act.isPending}
            onClick={() => act.mutate({ action: 'skip' })}>Skip this step</button>}
          {isOpen && <button type="button" style={btn} onClick={() => setStopping((v) => !v)}>Stop…</button>}
          {!isOpen && <button type="button" style={btn} disabled={act.isPending} onClick={() => act.mutate({ action: 'restart' })}>Start again</button>}
        </div>
        {stopping && (
          <div style={{ display: 'flex', gap: 8 }}>
            <input aria-label="Why it is stopped" style={input} value={reason} placeholder="Why? e.g. reached them, not interested, wrong number"
              onChange={(e) => setReason(e.target.value)} />
            <button type="button" style={primary} disabled={!reason.trim() || act.isPending}
              onClick={() => act.mutate({ action: 'stop', reason })}>Stop</button>
          </div>
        )}
        {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>{error}</div>}
      </div>

      <div style={card}>
        <div style={{ fontWeight: 600, color: INK, marginBottom: 8 }}>Steps</div>
        <ol style={{ margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {d.steps.map((s, i) => (
            <li key={i} style={{ fontSize: 13, color: s.status === 'pending' ? MUTED : INK }}>
              <span style={{ fontWeight: s.status === 'current' ? 600 : 400 }}>{s.label}</span>
              {s.status === 'done' && <span style={{ color: 'rgb(2,122,72)' }}> · done</span>}
              {s.status === 'current' && d.next_at && <span style={{ color: d.overdue ? RED : MUTED }}> · {when(d.next_at)} ({relative(d.next_at)})</span>}
              {s.template && <div style={{ color: MUTED, fontSize: 12, marginTop: 2 }}>“{s.template}”</div>}
            </li>
          ))}
        </ol>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 600, color: INK, marginBottom: 8 }}>What happened</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {d.events.map((e) => (
            <div key={e.id} style={{ display: 'flex', gap: 10, fontSize: 13 }}>
              <span aria-hidden style={{ width: 8, height: 8, borderRadius: 4, marginTop: 6, flexShrink: 0, background: eventTone(e.kind, e.state) }} />
              <div style={{ minWidth: 0 }}>
                <div style={{ color: MUTED, fontSize: 12 }}>{when(e.at)} · {EVENT_LABEL[e.kind] ?? e.kind}{e.state === 'shadow' ? ' (shadow — nothing sent)' : ''}{e.who ? ` · ${e.who}` : ''}</div>
                <div style={{ color: INK, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{e.text}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---- sequences ---------------------------------------------------------------------------------

const BOARDS = ['AHS Simple Flow', 'AHS - Inspection', 'AHS - Repair & Review', 'Retail']

function Sequences({ admin }: { admin: boolean }) {
  const q = useQuery({ queryKey: ['followup-sequences'], queryFn: followupSequences })
  const [editing, setEditing] = useState<FollowupSequence | 'new' | null>(null)
  return (
    <div style={{ overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 900 }}>
      <div style={{ fontSize: 13, color: MUTED }}>
        Each board starts its unanswered calls on its default sequence; a number with no job uses General.
        A running follow-up keeps the steps it started with. {admin ? '' : 'Only an admin can change a sequence.'}
      </div>
      {admin && editing === null && <div><button type="button" style={primary} onClick={() => setEditing('new')}>New sequence</button></div>}
      {editing !== null && <SequenceEditor seq={editing === 'new' ? null : editing} onDone={() => setEditing(null)} />}
      {q.data?.sequences.map((s) => (
        <div key={s.id} style={{ ...card, opacity: s.active ? 1 : 0.6 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
            <span style={{ fontWeight: 600, color: INK }}>{s.name}</span>
            <span style={{ fontSize: 12, color: MUTED }}>{s.board ?? 'General'}{s.is_default ? ' · default' : ''}{s.active ? '' : ' · off'}</span>
            {admin && <button type="button" style={{ ...btn, marginLeft: 'auto' }} onClick={() => setEditing(s)}>Edit</button>}
          </div>
          <ol style={{ margin: '6px 0 0', paddingLeft: 20, fontSize: 13, color: INK }}>
            {s.labels.map((l, i) => <li key={i}>{l}{s.steps[i]?.template && <div style={{ color: MUTED, fontSize: 12 }}>“{s.steps[i].template}”</div>}</li>)}
          </ol>
        </div>
      ))}
    </div>
  )
}

function SequenceEditor({ seq, onDone }: { seq: FollowupSequence | null; onDone: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(seq?.name ?? '')
  const [board, setBoard] = useState<string>(seq?.board ?? '')
  const [isDefault, setDefault] = useState(seq?.is_default ?? false)
  const [active, setActive] = useState(seq?.active ?? true)
  const [steps, setSteps] = useState<FollowupStep[]>(seq?.steps ?? [{ type: 'text', delay_minutes: 5, template: 'Hi {first_name}, this is Dream Team Roofing. ' }])
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => followupSaveSequence(seq?.id ?? null, { name, board: board || null, is_default: isDefault, active, steps }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['followup-sequences'] }); onDone() },
    onError: (e: Error) => setError(e.message),
  })
  const set = (i: number, patch: Partial<FollowupStep>) => setSteps(steps.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  return (
    <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10 }}>
        <label style={{ fontSize: 12, color: MUTED }}>Name<input style={{ ...input, marginTop: 4 }} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label style={{ fontSize: 12, color: MUTED }}>Board
          <select style={{ ...input, marginTop: 4 }} value={board} onChange={(e) => setBoard(e.target.value)}>
            <option value="">General (no job / other boards)</option>
            {BOARDS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
      </div>
      <div style={{ display: 'flex', gap: 16, fontSize: 13, color: INK }}>
        <label><input type="checkbox" checked={isDefault} onChange={(e) => setDefault(e.target.checked)} /> Default for this board</label>
        <label><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> On</label>
      </div>
      {steps.map((s, i) => {
        const days = s.delay_days != null
        return (
          <div key={i} style={{ border: `1px solid ${LINE}`, borderRadius: 6, padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
              <strong>Step {i + 1}</strong>
              <select aria-label={`Step ${i + 1} type`} style={{ ...input, width: 130 }} value={s.type}
                onChange={(e) => {
                  const type = e.target.value as FollowupStepType
                  set(i, { type, template: type === 'text' ? (s.template ?? '') : undefined })
                }}>
                <option value="text">Text</option><option value="call_me">Call (me)</option><option value="ai_call">AI call</option>
              </select>
              <select aria-label={`Step ${i + 1} timing`} style={{ ...input, width: 150 }} value={days ? 'days' : 'minutes'}
                onChange={(e) => set(i, e.target.value === 'days' ? { delay_days: 1, at: '10:00', delay_minutes: undefined } : { delay_minutes: 30, delay_days: undefined, at: undefined })}>
                <option value="minutes">minutes after</option><option value="days">days later at</option>
              </select>
              {days ? <>
                <input aria-label={`Step ${i + 1} days`} type="number" min={1} max={60} style={{ ...input, width: 70 }} value={s.delay_days}
                  onChange={(e) => set(i, { delay_days: Number(e.target.value) })} />
                <input aria-label={`Step ${i + 1} time`} type="time" style={{ ...input, width: 120 }} value={s.at ?? '10:00'}
                  onChange={(e) => set(i, { at: e.target.value })} />
              </> : <input aria-label={`Step ${i + 1} minutes`} type="number" min={0} max={1440} style={{ ...input, width: 90 }} value={s.delay_minutes ?? 0}
                onChange={(e) => set(i, { delay_minutes: Number(e.target.value) })} />}
              <button type="button" style={{ ...btn, marginLeft: 'auto' }} onClick={() => setSteps(steps.filter((_, j) => j !== i))}>Remove</button>
            </div>
            {s.type === 'text' && <textarea aria-label={`Step ${i + 1} text`} rows={2} style={input} value={s.template ?? ''}
              onChange={(e) => set(i, { template: e.target.value })} />}
          </div>
        )
      })}
      <div style={{ fontSize: 12, color: MUTED }}>{'{first_name}'} becomes the customer's first name. Texts and AI calls only go out 8 AM–8 PM Monday–Saturday.</div>
      {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" style={btn} onClick={() => setSteps([...steps, { type: 'call_me', delay_days: 1, at: '10:00' }])}>Add step</button>
        <button type="button" style={{ ...primary, marginLeft: 'auto' }} disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>Save</button>
        <button type="button" style={btn} onClick={onDone}>Cancel</button>
      </div>
    </div>
  )
}
