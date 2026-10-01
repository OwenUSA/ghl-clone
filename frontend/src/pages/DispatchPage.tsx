import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { PageTabs } from '../components/PageTabs'
import { BLUE, INK, LINE, PAGE_BG } from '../components/ai/aiUi'
import { closeDispatchItem, dispatchItems, dispatchSummary, type DispatchItem } from '../lib/api'
import type { Me } from '../lib/auth'
import { canOpenAiAgents } from '../lib/aiAgents'
import { checkedLabel, dueLabel, formatPhone, queueLabel } from '../lib/dispatch'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const RED_BG = 'rgb(254,243,242)'
const ALL = 'urgent'

/**
 * Dispatch (2026-09-30): what the office has to do next, from Zuper's three pipelines and every
 * call and text (Quo, the CRM line, Zuper Connect). Fixed rules find the work
 * (app/dispatch/rules.py); this page lists it by queue, most overdue first.
 *
 * Phase 1 changes nothing in Zuper. "Open in Zuper" opens the job; "Done" means the office did
 * it; "Wrong" means it should not have been flagged — the count the owner watches before
 * anything is ever allowed to write. An item also clears by itself once Zuper shows the change.
 * ADMIN and unrestricted DISPATCHER only, like AI Agents (the API refuses everyone else).
 */
export function DispatchPage({ user }: { user: Me }) {
  const allowed = canOpenAiAgents(user)
  const [tab, setTab] = useState<string>(ALL)
  const summary = useQuery({ queryKey: ['dispatch-summary'], queryFn: dispatchSummary,
    enabled: allowed, refetchInterval: 60000, refetchOnWindowFocus: true })
  const items = useQuery({ queryKey: ['dispatch-items', tab],
    queryFn: () => dispatchItems(tab === ALL ? null : tab), enabled: allowed,
    refetchInterval: 60000, refetchOnWindowFocus: true })
  if (!allowed) return null

  const s = summary.data
  const queues = s?.queues ?? []
  const shown = (items.data?.items ?? []).filter((i) => tab !== ALL || i.urgent)
  const tabs = [
    { key: ALL, label: s ? `Urgent${s.urgent ? ` ${s.urgent}` : ''}` : 'Urgent',
      onSelect: () => setTab(ALL) },
    ...queues.map((q) => ({ key: q.key, label: queueLabel(q), onSelect: () => setTab(q.key) })),
  ]

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
      <div style={{ overflowY: 'auto', padding: '16px 24px', flex: 1 }}>
        {items.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>}
        {items.isError && <div style={{ color: RED, fontSize: 13 }}>
          Could not load the list: {(items.error as Error).message}</div>}
        {!items.isLoading && shown.length === 0 && (
          <div style={{ color: MUTED, fontSize: 13, padding: 24, textAlign: 'center' }}>
            {tab === ALL ? 'Nothing overdue right now.' : 'Nothing here — good.'}
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 980 }}>
          {shown.map((i) => <Row key={i.id} item={i} />)}
        </div>
      </div>
    </div>
  )
}

function Row({ item }: { item: DispatchItem }) {
  const qc = useQueryClient()
  const [asking, setAsking] = useState<null | 'done' | 'wrong'>(null)
  const [note, setNote] = useState('')
  const close = useMutation({
    mutationFn: (how: 'done' | 'wrong') => closeDispatchItem(item.id, how, note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dispatch-items'] })
      qc.invalidateQueries({ queryKey: ['dispatch-summary'] })
    },
  })
  const said = item.evidence.summary || item.evidence.text
  const due = dueLabel(item.due_at)
  return (
    <div style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 8,
      borderLeft: `4px solid ${item.urgent ? 'rgb(240,68,56)' : 'rgb(208,213,221)'}`,
      padding: '12px 16px' }}>
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
      {item.todo && <div style={{ fontSize: 13, color: INK, marginTop: 8 }}>
        <span style={{ fontWeight: 600 }}>To do: </span>{item.todo}</div>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10,
        fontSize: 13, flexWrap: 'wrap' }}>
        {item.zuper_url && <a href={item.zuper_url} target="_blank" rel="noreferrer"
          style={{ color: BLUE, fontWeight: 500 }}>Open #{item.job_number} in Zuper ↗</a>}
        {item.phone && <span style={{ color: MUTED }}>{formatPhone(item.phone)}</span>}
        <span className="ml-auto" />
        {asking ? (
          <>
            <input autoFocus value={note} onChange={(e) => setNote(e.target.value)}
              placeholder={asking === 'done' ? 'What was done (optional)' : 'Why is it wrong? (optional)'}
              style={{ border: `1px solid ${LINE}`, borderRadius: 6, padding: '4px 8px',
                fontSize: 13, minWidth: 240 }} />
            <button type="button" disabled={close.isPending}
              onClick={() => close.mutate(asking)}
              style={{ color: '#fff', background: asking === 'done' ? BLUE : RED,
                borderRadius: 6, padding: '4px 10px', fontWeight: 500 }}>
              {asking === 'done' ? 'Mark done' : 'Mark wrong'}
            </button>
            <button type="button" onClick={() => { setAsking(null); setNote('') }}
              style={{ color: MUTED }}>Cancel</button>
          </>
        ) : (
          <>
            <button type="button" onClick={() => setAsking('done')}
              style={{ color: BLUE, fontWeight: 500 }}>Done</button>
            <button type="button" onClick={() => setAsking('wrong')}
              style={{ color: MUTED }}>Wrong</button>
          </>
        )}
      </div>
      {close.isError && <div style={{ color: RED, fontSize: 12, marginTop: 6 }}>
        {(close.error as Error).message}</div>}
    </div>
  )
}
