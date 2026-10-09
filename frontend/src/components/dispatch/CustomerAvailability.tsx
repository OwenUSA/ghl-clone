import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import {
  clearDispatchAvailability, dispatchAvailability, refreshDispatchAvailability,
  setDispatchAvailability, type DispatchAvailability, type DispatchLimits,
} from '../../lib/api'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const AMBER = 'rgb(181,71,8)'
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const field: React.CSSProperties = { border: `1px solid ${LINE}`, borderRadius: 6,
  padding: '2px 6px', fontSize: 12, background: '#fff' }
const link: React.CSSProperties = { color: BLUE, fontWeight: 500, fontSize: 12 }

/**
 * What each customer said about WHEN they can have the visit (2026-10-08): read by the AI from
 * all their calls and texts, applied to every plan. The office can correct a reading; the
 * correction stands until it is cleared. Shown inside "Remake the schedule".
 */
export function CustomerAvailability({ onChanged }: { onChanged: () => void }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [all, setAll] = useState(false)
  const q = useQuery({ queryKey: ['dispatch-availability'], queryFn: dispatchAvailability,
    enabled: open, staleTime: 30000 })
  const refresh = useMutation({ mutationFn: refreshDispatchAvailability,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['dispatch-availability'] }); onChanged() } })
  const jobs = q.data?.jobs ?? []
  const said = jobs.filter((j) => j.in_words || j.summary)
  const unread = jobs.filter((j) => !j.read_at).length
  const shown = all ? jobs : said
  return (
    <div style={{ marginTop: 12, border: `1px solid ${LINE}`, borderRadius: 8, padding: '8px 10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          style={{ fontSize: 13, fontWeight: 600, color: INK }}>
          {open ? '▾' : '▸'} What customers said about when they can have the visit</button>
        {open && q.data && <span style={{ fontSize: 12, color: MUTED }}>
          {said.length} with limits · {unread} not read yet</span>}
        {open && <span className="ml-auto" />}
        {open && <label style={{ fontSize: 12, color: MUTED }}>
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Show every job</label>}
        {open && <button type="button" onClick={() => refresh.mutate()} disabled={refresh.isPending}
          style={{ ...link, opacity: refresh.isPending ? 0.5 : 1 }}>
          {refresh.isPending ? 'Reading…' : 'Read the calls now'}</button>}
      </div>
      {open && refresh.isError && <div style={{ color: RED, fontSize: 12, marginTop: 4 }}>
        {(refresh.error as Error).message}</div>}
      {open && typeof refresh.data?.skipped === 'string' && <div style={{ color: AMBER, fontSize: 12,
        marginTop: 4 }}>Not read: {String(refresh.data.skipped)}</div>}
      {open && q.isLoading && <div style={{ color: MUTED, fontSize: 12, marginTop: 6 }}>Loading…</div>}
      {open && q.data && shown.length === 0 && <div style={{ color: MUTED, fontSize: 12, marginTop: 6 }}>
        No customer has said anything about when — or their calls are not read yet.</div>}
      {open && shown.map((j) => <Row key={j.job_uid} j={j} onChanged={() => {
        qc.invalidateQueries({ queryKey: ['dispatch-availability'] }); onChanged() }} />)}
    </div>
  )
}

function Row({ j, onChanged }: { j: DispatchAvailability; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const clear = useMutation({ mutationFn: () => clearDispatchAvailability(j.job_number ?? ''),
    onSuccess: onChanged })
  return (
    <div style={{ borderTop: `1px solid ${LINE}`, padding: '6px 0', fontSize: 13 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <span style={{ fontWeight: 500, color: INK }}>#{j.job_number} {j.customer}</span>
        <span style={{ color: j.in_words ? INK : MUTED }}>{j.in_words || (j.read_at
          ? 'no limits said' : 'not read yet')}</span>
        {j.source === 'office' && <span style={{ fontSize: 11, color: BLUE }}>set by the office</span>}
        {j.newer_messages && <span style={{ fontSize: 11, color: AMBER }}>
          new messages since — check them</span>}
        {j.error && <span style={{ fontSize: 11, color: RED }}>{j.error}</span>}
        <span className="ml-auto" />
        <button type="button" style={link} onClick={() => setEditing(!editing)}>
          {editing ? 'Close' : 'Correct'}</button>
        {j.source === 'office' && <button type="button" style={link} onClick={() => clear.mutate()}>
          Use the calls again</button>}
      </div>
      {j.summary && <div style={{ fontSize: 12, color: MUTED }}>{j.summary}</div>}
      {j.evidence.map((e, i) => <div key={i} style={{ fontSize: 12, color: MUTED }}>
        {e.at} — “{e.quote}”</div>)}
      {editing && <Editor j={j} onSaved={() => { setEditing(false); onChanged() }} />}
    </div>
  )
}

function Editor({ j, onSaved }: { j: DispatchAvailability; onSaved: () => void }) {
  const l = j.limits
  const [notBefore, setNotBefore] = useState(l.not_before ?? '')
  const [awayFrom, setAwayFrom] = useState(l.blocked?.[0]?.from ?? '')
  const [awayTo, setAwayTo] = useState(l.blocked?.[0]?.to ?? '')
  const [notDays, setNotDays] = useState<string[]>(l.not_days ?? [])
  const [after, setAfter] = useState(l.after ?? '')
  const [before, setBefore] = useState(l.before ?? '')
  const [tech, setTech] = useState(l.technician ?? '')
  const [summary, setSummary] = useState(j.summary ?? '')
  const save = useMutation({
    mutationFn: () => {
      const limits: DispatchLimits = {}
      if (notBefore) limits.not_before = notBefore
      if (awayFrom && awayTo) limits.blocked = [{ from: awayFrom, to: awayTo }]
      if (notDays.length) limits.not_days = notDays
      if (after) limits.after = after
      if (before) limits.before = before
      if (tech) limits.technician = tech
      return setDispatchAvailability(j.job_number ?? '', limits, summary || null)
    },
    onSuccess: onSaved,
  })
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 6, fontSize: 12, color: MUTED,
      alignItems: 'center' }}>
      <label>Not before <input type="date" value={notBefore} onChange={(e) => setNotBefore(e.target.value)} style={field} /></label>
      <label>Away <input type="date" value={awayFrom} onChange={(e) => setAwayFrom(e.target.value)} style={field} />
        {' '}to <input type="date" value={awayTo} onChange={(e) => setAwayTo(e.target.value)} style={field} /></label>
      <span>Not on {DAYS.map((d) => (
        <label key={d} style={{ marginRight: 4 }}><input type="checkbox" checked={notDays.includes(d)}
          onChange={(e) => setNotDays(e.target.checked ? [...notDays, d] : notDays.filter((x) => x !== d))} />{d}</label>
      ))}</span>
      <label>After <input type="time" value={after} onChange={(e) => setAfter(e.target.value)} style={field} /></label>
      <label>Done by <input type="time" value={before} onChange={(e) => setBefore(e.target.value)} style={field} /></label>
      <label>Technician <select value={tech} onChange={(e) => setTech(e.target.value)} style={field}>
        <option value="">Either</option><option value="Owen">Owen</option><option value="Antonio">Antonio</option>
      </select></label>
      <label style={{ flexBasis: '100%' }}>Note <input value={summary} onChange={(e) => setSummary(e.target.value)}
        style={{ ...field, width: '70%' }} placeholder="e.g. traveling until the 18th (call 10/08)" /></label>
      <button type="button" onClick={() => save.mutate()} disabled={save.isPending}
        style={{ color: '#fff', background: BLUE, borderRadius: 6, padding: '2px 10px', fontSize: 12 }}>
        {save.isPending ? 'Saving…' : 'Save'}</button>
      {save.isError && <span style={{ color: RED }}>{(save.error as Error).message}</span>}
    </div>
  )
}
