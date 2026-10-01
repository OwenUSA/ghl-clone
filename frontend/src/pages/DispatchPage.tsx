import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { PageTabs } from '../components/PageTabs'
import { AssistantChat } from '../components/dispatch/AssistantChat'
import { BLUE, INK, LINE, PAGE_BG } from '../components/ai/aiUi'
import {
  applyDispatchSuggestion, bookDispatchSlot, closeDispatchItem, decideSuggestion,
  dispatchItems, dispatchSettings, dispatchSlots, dispatchSuggestions, dispatchSummary,
  dispatchWrites, explainDispatchItem, saveDispatchSettings, saveDispatchWrites,
  type DispatchItem, type DispatchSettings, type DispatchSlot, type DispatchSuggestion,
  type DispatchTechnician, type DispatchWrites,
} from '../lib/api'
import type { Me } from '../lib/auth'
import { canOpenAiAgents, isAiAdmin } from '../lib/aiAgents'
import { checkedLabel, dueLabel, formatPhone, queueLabel, slotLabel } from '../lib/dispatch'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const RED_BG = 'rgb(254,243,242)'
const GREEN = 'rgb(2,122,72)'
const URGENT = 'urgent'
const SUGGESTIONS = 'suggestions'
const ASK = 'ask'
const SETTINGS = 'settings'
const BOOKABLE = new Set(['book_visit', 'new_not_called', 'needs_date'])

const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`,
  borderRadius: 8, padding: '12px 16px' }
const link: React.CSSProperties = { color: BLUE, fontWeight: 500 }

/**
 * Dispatch (2026-09-30): what the office has to do next, from Zuper's three pipelines and every
 * call and text. Fixed rules find the work (app/dispatch/rules.py); phase 2 adds the AI's
 * explanation, suggested Zuper changes, three booking slots and a chat.
 *
 * "Approve" on a suggestion means "correct — I'll do it in Zuper"; it closes itself when Zuper
 * shows the value. "Wrong" is the accuracy count the owner watches. ADMIN + unrestricted
 * DISPATCHER, like AI Agents.
 *
 * Phase 3 (2026-10-01): "Approve and let the agent do it" / "Book this" appear ONLY when the
 * server gate (DISPATCH_ZUPER_WRITES) and the ADMIN's switches in Settings allow that action —
 * all off by default. The server decides; the buttons only follow `GET /api/dispatch/writes`.
 */
export function DispatchPage({ user }: { user: Me }) {
  const allowed = canOpenAiAgents(user)
  // The assistant opens first (2026-10-01, the owner's ask). The server keeps the chats; the
  // open one is remembered here (and in sessionStorage, as a convenience) so switching tabs
  // keeps it.
  const [tab, setTab] = useState<string>(ASK)
  const [activeChatId, setActiveChatId] = useState<number | null>(readActiveChat)
  useEffect(() => { writeActiveChat(activeChatId) }, [activeChatId])
  const summary = useQuery({ queryKey: ['dispatch-summary'], queryFn: dispatchSummary,
    enabled: allowed, refetchInterval: 60000, refetchOnWindowFocus: true })
  const sugs = useQuery({ queryKey: ['dispatch-suggestions'], queryFn: () => dispatchSuggestions(),
    enabled: allowed, refetchInterval: 60000 })
  const isQueue = ![SUGGESTIONS, ASK, SETTINGS].includes(tab)
  const items = useQuery({ queryKey: ['dispatch-items', tab],
    queryFn: () => dispatchItems(tab === URGENT ? null : tab), enabled: allowed && isQueue,
    refetchInterval: 60000, refetchOnWindowFocus: true })
  if (!allowed) return null

  const s = summary.data
  const openSugs = (sugs.data?.suggestions ?? []).filter((x) => x.state === 'open').length
  const tabs = [
    { key: ASK, label: 'Assistant' },
    { key: URGENT, label: s ? `Urgent${s.urgent ? ` ${s.urgent}` : ''}` : 'Urgent' },
    ...(s?.queues ?? []).map((q) => ({ key: q.key, label: queueLabel(q) })),
    { key: SUGGESTIONS, label: openSugs ? `Suggestions ${openSugs}` : 'Suggestions' },
    { key: SETTINGS, label: 'Settings' },
  ].map((t) => ({ ...t, onSelect: () => setTab(t.key) }))
  const shown = (items.data?.items ?? []).filter((i) => tab !== URGENT || i.urgent)

  return (
    <div className="flex min-w-0 flex-1 flex-col" style={{ height: '100vh', overflow: 'hidden',
      backgroundColor: PAGE_BG }}>
      <PageTabs title="Dispatch" label="Dispatch queues" active={tab} tabs={tabs} />
      <div style={{ padding: '10px 24px', fontSize: 12, color: MUTED, display: 'flex', gap: 16,
        borderBottom: `1px solid ${LINE}`, background: '#fff', flexWrap: 'wrap' }}>
        {s && !s.enabled && <span style={{ color: RED }}>
          Dispatch is switched off on this server (DISPATCH_ENABLED) — nothing is being read.</span>}
        {s && <span>{checkedLabel(s.heartbeat.last_success_at)} · reads Zuper every{' '}
          {Math.round(s.heartbeat.poll_seconds / 60 * 10) / 10} min · changes nothing in Zuper</span>}
        {s?.heartbeat.last_error && <span style={{ color: RED }}>
          Last read failed: {s.heartbeat.last_error}</span>}
        {!!s?.heartbeat.unknown_stages.length && <span>
          Stages this page does not know yet: {s.heartbeat.unknown_stages.join(', ')}</span>}
        {s && <span className="ml-auto">Last 30 days: {s.accuracy_30d.done} done ·{' '}
          {s.accuracy_30d.wrong} marked wrong</span>}
      </div>
      {tab === ASK ? (
        <AssistantChat user={user} activeChatId={activeChatId} setActiveChatId={setActiveChatId}
          onOpenSuggestions={() => setTab(SUGGESTIONS)} />
      ) : (
      <div style={{ overflowY: 'auto', padding: '16px 24px', flex: 1 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 980 }}>
          {tab === SUGGESTIONS ? <SuggestionsTab list={sugs.data?.suggestions ?? []} />
            : tab === SETTINGS ? <SettingsTab admin={isAiAdmin(user)} />
            : (
              <>
                {items.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>}
                {items.isError && <div style={{ color: RED, fontSize: 13 }}>
                  Could not load the list: {(items.error as Error).message}</div>}
                {!items.isLoading && shown.length === 0 && (
                  <div style={{ color: MUTED, fontSize: 13, padding: 24, textAlign: 'center' }}>
                    {tab === URGENT ? 'Nothing overdue right now.' : 'Nothing here — good.'}
                  </div>
                )}
                {shown.map((i) => <Row key={i.id} item={i} />)}
              </>
            )}
        </div>
      </div>
      )}
    </div>
  )
}

const ACTIVE_CHAT_KEY = 'dispatch.activeChat'

/** The chat open in this tab, if sessionStorage remembers one. Storage can throw; then none. */
function readActiveChat(): number | null {
  try {
    const n = Number(window.sessionStorage.getItem(ACTIVE_CHAT_KEY))
    return Number.isInteger(n) && n > 0 ? n : null
  } catch {
    return null
  }
}

function writeActiveChat(id: number | null) {
  try {
    if (id == null) window.sessionStorage.removeItem(ACTIVE_CHAT_KEY)
    else window.sessionStorage.setItem(ACTIVE_CHAT_KEY, String(id))
  } catch {
    // A private window or blocked storage: the chat is just not remembered.
  }
}

function useWrites() {
  return useQuery({ queryKey: ['dispatch-writes'], queryFn: dispatchWrites, staleTime: 30000 })
}

function useRefresh() {
  const qc = useQueryClient()
  return () => {
    for (const k of ['dispatch-items', 'dispatch-summary', 'dispatch-suggestions',
      'dispatch-writes']) {
      qc.invalidateQueries({ queryKey: [k] })
    }
  }
}

function Row({ item }: { item: DispatchItem }) {
  const refresh = useRefresh()
  const [asking, setAsking] = useState<null | 'done' | 'wrong'>(null)
  const [note, setNote] = useState('')
  const [slotsOpen, setSlotsOpen] = useState(false)
  const close = useMutation({ mutationFn: (how: 'done' | 'wrong') =>
    closeDispatchItem(item.id, how, note), onSuccess: refresh })
  const explain = useMutation({ mutationFn: () => explainDispatchItem(item.id),
    onSuccess: refresh })
  const said = item.evidence.summary || item.evidence.text
  const due = dueLabel(item.due_at)
  return (
    <div style={{ ...card, borderLeft: `4px solid ${item.urgent ? 'rgb(240,68,56)' : 'rgb(208,213,221)'}` }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: INK }}>{item.title}</span>
        {item.board && <span style={{ fontSize: 12, color: MUTED }}>{item.board}</span>}
        {due && <span style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 500,
          color: item.urgent ? RED : MUTED, background: item.urgent ? RED_BG : 'transparent',
          borderRadius: 4, padding: '1px 6px' }}>{due}</span>}
      </div>
      {item.why && <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>{item.why}</div>}
      {said && (
        <div style={{ fontSize: 13, color: INK, marginTop: 6, padding: '6px 10px',
          background: 'rgb(249,250,251)', borderRadius: 6, borderLeft: `3px solid ${LINE}` }}>
          “{said}”
        </div>
      )}
      {item.ai ? <AiBlock item={item} /> : item.todo && (
        <div style={{ fontSize: 13, color: INK, marginTop: 8 }}>
          <span style={{ fontWeight: 600 }}>To do: </span>{item.todo}</div>
      )}
      {item.ai_error && <div style={{ fontSize: 12, color: MUTED, marginTop: 4 }}>
        AI: {item.ai_error}</div>}
      {item.suggestions.map((sg) => <SuggestionLine key={sg.id} s={sg} />)}
      {slotsOpen && item.job_uid && <Slots jobUid={item.job_uid} />}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10,
        fontSize: 13, flexWrap: 'wrap' }}>
        {item.zuper_url && <a href={item.zuper_url} target="_blank" rel="noreferrer" style={link}>
          Open #{item.job_number} in Zuper ↗</a>}
        {item.phone && <span style={{ color: MUTED }}>{formatPhone(item.phone)}</span>}
        {item.job_uid && BOOKABLE.has(item.kind) && (
          <button type="button" onClick={() => setSlotsOpen((v) => !v)} style={link}>
            {slotsOpen ? 'Hide slots' : 'Find 3 slots'}</button>
        )}
        <button type="button" disabled={explain.isPending} onClick={() => explain.mutate()}
          style={{ color: MUTED }}>{explain.isPending ? 'Asking the AI…'
            : item.ai ? 'Ask the AI again' : 'Ask the AI'}</button>
        <span className="ml-auto" />
        {asking ? (
          <>
            <input autoFocus value={note} onChange={(e) => setNote(e.target.value)}
              placeholder={asking === 'done' ? 'What was done (optional)' : 'Why is it wrong? (optional)'}
              style={{ border: `1px solid ${LINE}`, borderRadius: 6, padding: '4px 8px',
                fontSize: 13, minWidth: 240 }} />
            <button type="button" disabled={close.isPending} onClick={() => close.mutate(asking)}
              style={{ color: '#fff', background: asking === 'done' ? BLUE : RED,
                borderRadius: 6, padding: '4px 10px', fontWeight: 500 }}>
              {asking === 'done' ? 'Mark done' : 'Mark wrong'}
            </button>
            <button type="button" onClick={() => { setAsking(null); setNote('') }}
              style={{ color: MUTED }}>Cancel</button>
          </>
        ) : (
          <>
            <button type="button" onClick={() => setAsking('done')} style={link}>Done</button>
            <button type="button" onClick={() => setAsking('wrong')} style={{ color: MUTED }}>Wrong</button>
          </>
        )}
      </div>
      {(close.isError || explain.isError) && <div style={{ color: RED, fontSize: 12, marginTop: 6 }}>
        {((close.error || explain.error) as Error).message}</div>}
    </div>
  )
}

function AiBlock({ item }: { item: DispatchItem }) {
  const a = item.ai!
  return (
    <div style={{ marginTop: 8, fontSize: 13, color: INK }}>
      {a.zuper_steps.length > 0 && (
        <>
          <div style={{ fontWeight: 600 }}>In Zuper:</div>
          <ol style={{ margin: '2px 0 0 18px', listStyle: 'decimal' }}>
            {a.zuper_steps.map((st, n) => <li key={n}>{st}</li>)}
          </ol>
        </>
      )}
      {a.say && <div style={{ marginTop: 6 }}><span style={{ fontWeight: 600 }}>Say: </span>{a.say}</div>}
      {a.note && <div style={{ marginTop: 4, color: MUTED }}>{a.note}</div>}
      <div style={{ fontSize: 11, color: MUTED, marginTop: 4 }}>
        Written by AI{a.confidence ? ` · ${a.confidence} confidence` : ''} — check it against the job.
        {item.todo && <> Rule: {item.todo}</>}
      </div>
    </div>
  )
}

function SuggestionLine({ s, showJob = false }: { s: DispatchSuggestion; showJob?: boolean }) {
  const refresh = useRefresh()
  const w = useWrites()
  const [result, setResult] = useState<{ ok: boolean; sentence: string } | null>(null)
  const decide = useMutation({ mutationFn: (how: 'approve' | 'wrong') => decideSuggestion(s.id, how),
    onSuccess: refresh })
  const apply = useMutation({ mutationFn: (approve: boolean) => applyDispatchSuggestion(s.id, approve),
    onSuccess: (r) => { setResult(r); refresh() } })
  const agentMay = !!w.data?.can[s.kind]
  const busy = decide.isPending || apply.isPending
  const what = s.kind === 'note' ? 'Add a note' : `${s.field}: ${s.current ? `“${s.current}” → ` : ''}“${s.proposed}”`
  const failed = s.state === 'apply_failed'
  return (
    <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, fontSize: 13,
      background: failed ? RED_BG : s.state === 'approved' ? 'rgb(236,253,243)' : 'rgb(245,248,255)' }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <span style={{ fontWeight: 600, color: INK }}>Suggested change</span>
        {showJob && <span style={{ color: MUTED }}>#{s.job_number} · {s.board}</span>}
        <span style={{ color: INK }}>{s.kind === 'note' ? `${what}: “${s.proposed}”` : what}</span>
        <span className="ml-auto" />
        {s.state === 'approved' ? (
          <>
            <span style={{ color: GREEN, fontSize: 12 }}>Approved — waiting to see it in Zuper</span>
            {agentMay && <button type="button" disabled={busy} onClick={() => apply.mutate(false)}
              style={link} title="The agent makes this change in Zuper and checks it took">
              {apply.isPending ? 'Making the change…' : 'Let the agent do it'}</button>}
          </>
        ) : (
          <>
            <button type="button" disabled={busy} onClick={() => decide.mutate('approve')}
              style={link} title="Correct — I'll make this change in Zuper">
              {failed ? 'Approve again' : 'Approve'}</button>
            {agentMay && !failed && <button type="button" disabled={busy}
              onClick={() => apply.mutate(true)} style={link}
              title="Correct — the agent makes this change in Zuper and checks it took">
              {apply.isPending ? 'Making the change…' : 'Approve and let the agent do it'}</button>}
            <button type="button" disabled={busy} onClick={() => decide.mutate('wrong')}
              style={{ color: MUTED }}>Wrong</button>
          </>
        )}
        {showJob && s.zuper_url && <a href={s.zuper_url} target="_blank" rel="noreferrer" style={link}>
          Open in Zuper ↗</a>}
      </div>
      {s.evidence && <div style={{ color: MUTED, fontSize: 12, marginTop: 2 }}>Because: {s.evidence}</div>}
      {(result || (failed && s.apply_result)) && (
        <div style={{ fontSize: 12, marginTop: 4, color: result?.ok ? GREEN : RED }}>
          {result ? result.sentence : s.apply_result}</div>
      )}
      {(apply.isError || decide.isError) && <div style={{ color: RED, fontSize: 12, marginTop: 4 }}>
        {((apply.error || decide.error) as Error).message}</div>}
    </div>
  )
}

function Slots({ jobUid }: { jobUid: string }) {
  const q = useQuery({ queryKey: ['dispatch-slots', jobUid], queryFn: () => dispatchSlots(jobUid) })
  const w = useWrites()
  const refresh = useRefresh()
  const [picked, setPicked] = useState<DispatchSlot | null>(null)
  const book = useMutation({ mutationFn: (sl: DispatchSlot) => bookDispatchSlot(jobUid,
    { start: sl.start, end: sl.end, technician: sl.tech }),
  onSuccess: () => { setPicked(null); refresh() } })
  const canBook = !!w.data?.can.booking
  return (
    <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: 'rgb(249,250,251)',
      fontSize: 13 }}>
      <div style={{ fontWeight: 600, color: INK }}>Offer the customer one of these</div>
      {q.isLoading && <div style={{ color: MUTED }}>Looking at the calendar…</div>}
      {q.isError && <div style={{ color: RED }}>{(q.error as Error).message}</div>}
      {q.data?.slots.length === 0 && <div style={{ color: MUTED }}>No free slot in the next 10 business days.</div>}
      <ul style={{ margin: '4px 0 0 0' }}>
        {q.data?.slots.map((sl, n) => (
          <li key={n} style={{ color: INK }}>{slotLabel(sl)}
            {canBook && picked !== sl && <button type="button" disabled={book.isPending}
              onClick={() => { setPicked(sl); book.reset() }} style={{ ...link, marginLeft: 8 }}>
              Book this</button>}
            {picked === sl && (
              <span style={{ marginLeft: 8 }}>
                <span style={{ color: MUTED }}>Book it in Zuper (time and Technician field)? </span>
                <button type="button" disabled={book.isPending} onClick={() => book.mutate(sl)}
                  style={{ color: '#fff', background: BLUE, borderRadius: 6, padding: '1px 8px',
                    fontWeight: 500 }}>{book.isPending ? 'Booking…' : 'Yes, book it'}</button>
                <button type="button" onClick={() => setPicked(null)}
                  style={{ color: MUTED, marginLeft: 6 }}>No</button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {book.data && <div style={{ color: book.data.ok ? GREEN : RED, fontSize: 12 }}>
        {book.data.sentence}</div>}
      {book.isError && <div style={{ color: RED, fontSize: 12 }}>{(book.error as Error).message}</div>}
      {q.data?.note && <div style={{ color: MUTED, fontSize: 12 }}>{q.data.note}</div>}
      <div style={{ color: MUTED, fontSize: 11, marginTop: 4 }}>
        {canBook ? 'Agree a slot with the customer, then book it here or in Zuper.'
          : 'Book the one the customer picks in Zuper (date, time and the Technician field).'}</div>
    </div>
  )
}

function SuggestionsTab({ list }: { list: DispatchSuggestion[] }) {
  if (list.length === 0) {
    return <div style={{ color: MUTED, fontSize: 13, padding: 24, textAlign: 'center' }}>
      No suggested changes. The AI adds them when a call shows Zuper is missing something.</div>
  }
  return <>{list.map((s) => <div key={s.id} style={card}><SuggestionLine s={s} showJob /></div>)}</>
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function SettingsTab({ admin }: { admin: boolean }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['dispatch-settings'], queryFn: dispatchSettings })
  const save = useMutation({ mutationFn: saveDispatchSettings,
    onSuccess: (d) => qc.setQueryData(['dispatch-settings'], d) })
  const [techs, setTechs] = useState<DispatchTechnician[] | null>(null)
  if (!q.data) return <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>
  const s: DispatchSettings = q.data
  const rows = techs ?? s.technicians
  const set = (n: number, patch: Partial<DispatchTechnician>) =>
    setTechs(rows.map((t, i) => (i === n ? { ...t, ...patch } : t)))
  const field = { border: `1px solid ${LINE}`, borderRadius: 6, padding: '3px 6px', fontSize: 13 }
  return (
    <>
      <div style={card}>
        <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>AI</div>
        <div style={{ fontSize: 13, color: MUTED, margin: '4px 0 10px' }}>
          When on, the AI explains each new item and answers the Ask tab. It sends the job's
          details and the calls' summaries and transcripts to the chosen provider. It never
          changes Zuper. {s.runs_today} of {s.daily_cap} AI requests used today.</div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
          <label>Connection{' '}
            <select disabled={!admin} value={s.connection_id ?? ''} style={field}
              onChange={(e) => save.mutate({ connection_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">— none —</option>
              {s.connections.map((c) => <option key={c.id} value={c.id}>
                {c.name} ({c.provider} ••••{c.last4})</option>)}
            </select></label>
          <label>Model <input disabled={!admin} defaultValue={s.model} style={field}
            onBlur={(e) => e.target.value !== s.model && save.mutate({ model: e.target.value })} /></label>
          <label>Daily limit <input disabled={!admin} type="number" defaultValue={s.daily_cap}
            style={{ ...field, width: 80 }}
            onBlur={(e) => Number(e.target.value) !== s.daily_cap && save.mutate({ daily_cap: Number(e.target.value) })} /></label>
          <button type="button" disabled={!admin || save.isPending}
            onClick={() => save.mutate({ ai_enabled: !s.ai_enabled })}
            style={{ color: '#fff', background: s.ai_enabled ? RED : BLUE, borderRadius: 6,
              padding: '4px 12px', fontWeight: 500 }}>
            {s.ai_enabled ? 'Switch AI off' : 'Switch AI on'}</button>
          <span style={{ color: s.ai_enabled ? GREEN : MUTED }}>{s.ai_enabled ? 'On' : 'Off'}</span>
        </div>
        {!s.connections.length && <div style={{ fontSize: 12, color: MUTED, marginTop: 6 }}>
          Add an AI connection first (AI Agents → Settings → AI Connections).</div>}
      </div>
      <div style={card}>
        <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>Booking — who goes, when</div>
        <div style={{ fontSize: 13, color: MUTED, margin: '4px 0 10px' }}>
          The three slots go to the technician who prefers that kind of visit first, next to
          what he already has that day.</div>
        <table style={{ fontSize: 13, borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr style={{ color: MUTED, textAlign: 'left' }}>
            <th>Technician</th><th>Prefers</th><th>Days</th><th>From</th><th>To</th><th>Max/day</th></tr></thead>
          <tbody>
            {rows.map((t, n) => (
              <tr key={t.name}>
                <td style={{ padding: '4px 0' }}>{t.name}</td>
                <td><select disabled={!admin} value={t.prefers} style={field}
                  onChange={(e) => set(n, { prefers: e.target.value })}>
                  <option value="repair">Repairs</option><option value="inspection">Inspections</option>
                </select></td>
                <td>{DAYS.map((d, i) => (
                  <label key={d} style={{ marginRight: 4 }}>
                    <input type="checkbox" disabled={!admin} checked={t.days.includes(i)}
                      onChange={(e) => set(n, { days: e.target.checked ? [...t.days, i].sort()
                        : t.days.filter((x) => x !== i) })} /> {d}</label>))}</td>
                <td><input disabled={!admin} value={t.start} style={{ ...field, width: 60 }}
                  onChange={(e) => set(n, { start: e.target.value })} /></td>
                <td><input disabled={!admin} value={t.end} style={{ ...field, width: 60 }}
                  onChange={(e) => set(n, { end: e.target.value })} /></td>
                <td><input disabled={!admin} type="number" value={t.max} style={{ ...field, width: 50 }}
                  onChange={(e) => set(n, { max: Number(e.target.value) })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {admin && techs && (
          <button type="button" disabled={save.isPending}
            onClick={() => save.mutate({ technicians: techs }, { onSuccess: () => setTechs(null) })}
            style={{ marginTop: 10, color: '#fff', background: BLUE, borderRadius: 6,
              padding: '4px 12px', fontWeight: 500 }}>Save booking rules</button>
        )}
      </div>
      {save.isError && <div style={{ color: RED, fontSize: 13 }}>{(save.error as Error).message}</div>}
      <WritesSection admin={admin} />
      {!admin && <div style={{ color: MUTED, fontSize: 12 }}>Only an admin can change these.</div>}
    </>
  )
}

/**
 * "Let the agent make changes in Zuper" (phase 3). Turning a switch ON opens a confirmation
 * built into the page — type the phrase the server asks for; turning one OFF is one click.
 * The server gate is the deployment's (DISPATCH_ZUPER_WRITES) and is only reported here.
 */
function WritesSection({ admin }: { admin: boolean }) {
  const qc = useQueryClient()
  const w = useWrites()
  const [asking, setAsking] = useState<{ key: string; label: string } | null>(null)
  const [typed, setTyped] = useState('')
  const save = useMutation({ mutationFn: saveDispatchWrites,
    onSuccess: (d: DispatchWrites) => {
      qc.setQueryData(['dispatch-writes'], d)
      qc.invalidateQueries({ queryKey: ['dispatch-settings'] })
      setAsking(null)
      setTyped('')
    } })
  if (!w.data) return null
  const d = w.data
  const master = d.switches[0]?.key
  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>Let the agent make changes in Zuper</div>
      <div style={{ fontSize: 13, color: MUTED, margin: '4px 0 8px' }}>
        After a person confirms a suggested change, a slot or a stage, the agent can make it in
        Zuper and check it took. It never deletes, never touches pictures, never cancels or
        closes a job, never messages a customer and never touches quotes, invoices or payments.
        Every change is recorded below. Run the Zuper safety check before turning any of this on.</div>
      <div style={{ fontSize: 13, marginBottom: 8, color: d.server_gate ? GREEN : RED }}>
        {d.server_gate ? 'This server allows it (DISPATCH_ZUPER_WRITES is on).'
          : d.server_gate_sentence}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
        {d.switches.map((sw) => (
          <div key={sw.key} style={{ display: 'flex', alignItems: 'center', gap: 10,
            paddingLeft: sw.key === master ? 0 : 18 }}>
            <span style={{ color: INK, fontWeight: sw.key === master ? 600 : 400, flex: 1 }}>
              {sw.label}{sw.key === 'write_address' && ' (not yet tested against Zuper — leave off)'}</span>
            <span style={{ color: sw.on ? GREEN : MUTED, width: 28 }}>{sw.on ? 'On' : 'Off'}</span>
            <button type="button" disabled={!admin || save.isPending}
              onClick={() => {
                if (sw.on) save.mutate({ [sw.key]: false })
                else { setAsking({ key: sw.key, label: sw.label }); setTyped('') }
              }}
              style={{ color: sw.on ? RED : BLUE, fontWeight: 500, width: 80, textAlign: 'right' }}>
              {sw.on ? 'Turn off' : 'Turn on'}</button>
          </div>
        ))}
      </div>
      {asking && (
        <div role="dialog" aria-label="Confirm turning on" style={{ marginTop: 10, padding: 12,
          border: `1px solid ${RED}`, borderRadius: 8, background: RED_BG, fontSize: 13 }}>
          <div style={{ fontWeight: 600, color: INK }}>Turn on “{asking.label}”?</div>
          <div style={{ color: INK, margin: '4px 0 8px' }}>
            This lets the agent change jobs in Zuper — the business's source of truth — when a
            person tells it to. Type <b>{d.confirm_phrase}</b> to confirm.</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)}
              aria-label="Confirmation phrase" style={{ border: `1px solid ${LINE}`,
                borderRadius: 6, padding: '4px 8px', fontSize: 13, width: 140 }} />
            <button type="button" disabled={typed !== d.confirm_phrase || save.isPending}
              onClick={() => save.mutate({ [asking.key]: true, confirm: typed })}
              style={{ color: '#fff', background: typed === d.confirm_phrase ? RED : 'rgb(208,213,221)',
                borderRadius: 6, padding: '4px 12px', fontWeight: 500 }}>Turn on</button>
            <button type="button" onClick={() => { setAsking(null); setTyped('') }}
              style={{ color: MUTED }}>Cancel</button>
          </div>
        </div>
      )}
      {save.isError && <div style={{ color: RED, fontSize: 12, marginTop: 6 }}>
        {(save.error as Error).message}</div>}
      {d.recent.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 12, color: MUTED }}>
          <div style={{ fontWeight: 600, color: INK }}>Recent</div>
          {d.recent.map((r) => (
            <div key={r.id}>
              {r.at ? new Date(r.at).toLocaleString('en-US', { timeZone: 'America/New_York' }) : ''}
              {' · '}{r.action === 'switch' ? `${r.target}: ${r.old} → ${r.new}`
                : `#${r.job_number ?? '?'} ${r.action} — ${r.result}: ${r.sentence ?? ''}`}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

