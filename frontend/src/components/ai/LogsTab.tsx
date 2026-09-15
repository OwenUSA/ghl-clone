import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { aiAgents, aiMetrics, aiRun, aiRuns, type AiStep } from '../../lib/api'
import type { Me } from '../../lib/auth'
import {
  ACTION_STATUS_LABEL, bars, formatCost, formatLatency, formatTokens, isAiAdmin, modeLabel,
  OUTCOME_LABEL, OUTCOME_TONE, outcomeLabel, outcomeRows, stamp,
} from '../../lib/aiAgents'
import {
  BLUE, Chip, Empty, FAINT, INK, INPUT, LINE, Modal, MUTED, PageHeader, PAGE_BG, smallButton,
  SubTabs, TableCard, Td, TEXT, Th,
} from './aiUi'

/** Agent Logs (ADMIN): every run, filterable, with its full transcript — and Metrics. */
export function LogsTab({ user }: { user: Me }) {
  const [view, setView] = useState<'logs' | 'metrics'>('logs')
  if (!isAiAdmin(user)) return null
  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 16px' }}>
      <PageHeader title="Agent Logs"
        subtitle="Every time an agent was triggered — including when it decided not to act, and why." />
      <div style={{ marginTop: 12 }}>
        <SubTabs label="Logs views" active={view} onSelect={setView}
          tabs={[{ key: 'logs', label: 'Logs' }, { key: 'metrics', label: 'Metrics' }]} />
      </div>
      {view === 'logs' ? <Logs /> : <Metrics />}
    </div>
  )
}

function Logs() {
  const agents = useQuery({ queryKey: ['ai-agents'], queryFn: aiAgents })
  const [agentId, setAgentId] = useState('')
  const [outcome, setOutcome] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [tests, setTests] = useState(true)
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<number | null>(null)
  const runs = useQuery({
    queryKey: ['ai-runs', agentId, outcome, since, until, tests, page],
    queryFn: () => aiRuns({ agent_id: agentId ? Number(agentId) : null, outcome, since, until,
      include_tests: tests, page, page_size: 50 }),
    refetchInterval: 15000,
  })
  const filter: React.CSSProperties = { ...INPUT, marginTop: 0, height: 30, fontSize: 13, width: 'auto' }
  const data = runs.data
  return (
    <>
      <TableCard toolbar={
        <>
          <select aria-label="Agent" value={agentId} onChange={(e) => { setAgentId(e.target.value); setPage(1) }} style={filter}>
            <option value="">All agents</option>
            {(agents.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <select aria-label="Outcome" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(1) }} style={filter}>
            <option value="">All outcomes</option>
            {Object.entries(OUTCOME_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label className="flex items-center gap-4" style={{ fontSize: 13, color: MUTED }}>
            From <input type="date" aria-label="From" value={since} onChange={(e) => { setSince(e.target.value); setPage(1) }} style={filter} />
          </label>
          <label className="flex items-center gap-4" style={{ fontSize: 13, color: MUTED }}>
            To <input type="date" aria-label="To" value={until} onChange={(e) => { setUntil(e.target.value); setPage(1) }} style={filter} />
          </label>
          <label className="flex items-center gap-8" style={{ fontSize: 13, color: TEXT }}>
            <input type="checkbox" checked={tests} onChange={(e) => { setTests(e.target.checked); setPage(1) }}
              style={{ accentColor: BLUE }} /> Include Try-it tests
          </label>
          {data && <span className="ml-auto" style={{ fontSize: 12, color: MUTED }}>{data.total} run{data.total === 1 ? '' : 's'}</span>}
        </>
      } footer={data && data.pages > 1 ? (
        <div className="flex items-center justify-end gap-8" style={{ padding: '6px 8px', fontSize: 12 }}>
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} style={{ ...smallButton, height: 24 }}>Previous</button>
          <span>Page {data.page} of {data.pages}</span>
          <button type="button" disabled={page >= data.pages} onClick={() => setPage(page + 1)} style={{ ...smallButton, height: 24 }}>Next</button>
        </div>
      ) : undefined}>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 1140, tableLayout: 'fixed' }}>
          <thead><tr>
            <Th width={130}>Time</Th><Th width={150}>Agent</Th><Th width={140}>Trigger</Th><Th width={210}>Subject</Th>
            <Th width={80}>Mode</Th><Th>Outcome</Th><Th width={170}>Tokens</Th>
            <Th width={90} align="right">Cost</Th><Th width={80} align="right">Latency</Th>
          </tr></thead>
          <tbody>
            {(data?.items ?? []).map((r) => (
              <tr key={r.id} onClick={() => setOpen(r.id)} className="cursor-pointer hover:bg-[rgb(249,250,251)]"
                data-run={r.id}>
                <Td>{stamp(r.created_at)}</Td>
                <Td>{r.agent_name}{r.version != null && <span style={{ color: FAINT }}> v{r.version}</span>}</Td>
                <Td>{r.trigger_label}</Td>
                <Td>{r.subject || '—'}</Td>
                <Td>
                  {r.is_test ? <Chip text="Test" fg="rgb(83,56,158)" bg="rgb(244,243,255)" /> : modeLabel(r.mode)}
                </Td>
                <Td>
                  <Chip text={outcomeLabel(r.outcome)} {...(OUTCOME_TONE[r.outcome] ?? OUTCOME_TONE.queued)} />
                  {r.reason && <div style={{ fontSize: 12, color: MUTED, marginTop: 4, maxHeight: 36, overflow: 'hidden' }}>{r.reason}</div>}
                </Td>
                <Td><span style={{ fontSize: 12 }}>{r.provider ? formatTokens(r.tokens) : '—'}</span></Td>
                <Td align="right">{r.provider ? formatCost(r.cost) : '—'}</Td>
                <Td align="right">{formatLatency(r.latency_ms)}</Td>
              </tr>
            ))}
            {runs.isSuccess && (data?.items ?? []).length === 0 && (
              <tr><td colSpan={9}><Empty>No runs match.</Empty></td></tr>
            )}
          </tbody>
        </table>
      </TableCard>
      {open != null && <RunDetail runId={open} onClose={() => setOpen(null)} />}
    </>
  )
}

function StepView({ s }: { s: AiStep }) {
  const [expanded, setExpanded] = useState(false)
  const box: React.CSSProperties = { padding: '8px 10px', borderRadius: 8, fontSize: 13, whiteSpace: 'pre-wrap',
    lineHeight: 1.45, border: `1px solid ${LINE}`, backgroundColor: '#fff', color: TEXT }
  const heading = (t: string, color = FAINT) => (
    <div style={{ fontSize: 11, fontWeight: 600, color, textTransform: 'uppercase', letterSpacing: 0.3, marginBottom: 4 }}>{t}</div>
  )
  if (s.kind === 'system') {
    return (
      <div style={{ marginBottom: 10 }}>
        {heading('System prompt')}
        <button type="button" onClick={() => setExpanded(!expanded)} style={{ fontSize: 13, color: BLUE }}>
          {expanded ? 'Hide the compiled prompt' : 'Show the compiled prompt'}
        </button>
        {expanded && <pre style={{ ...box, marginTop: 6, fontSize: 12, backgroundColor: PAGE_BG }}>{s.text}</pre>}
      </div>
    )
  }
  if (s.kind === 'user' || s.kind === 'assistant') {
    const calls = (s.data?.tool_calls as { name: string }[] | undefined) ?? []
    if (!s.text && calls.length === 0) return null
    return (
      <div style={{ marginBottom: 10 }}>
        {heading(s.kind === 'user' ? 'Message to the agent' : 'Agent')}
        {s.text && <div style={box}>{s.text}</div>}
      </div>
    )
  }
  if (s.kind === 'tool_call') {
    return (
      <div style={{ marginBottom: 10 }} data-step="tool_call">
        {heading('Tool call · ' + (s.tool_name ?? ''), 'rgb(21,94,239)')}
        <pre style={{ ...box, fontSize: 12, backgroundColor: 'rgb(239,244,255)' }}>
          {JSON.stringify((s.data as { arguments?: unknown } | null)?.arguments ?? {}, null, 2)}
        </pre>
      </div>
    )
  }
  if (s.kind === 'tool_result') {
    return (
      <div style={{ marginBottom: 10 }}>
        {heading('Tool result · ' + (s.tool_name ?? ''))}
        <pre style={{ ...box, fontSize: 12, backgroundColor: PAGE_BG, maxHeight: expanded ? undefined : 120, overflow: 'hidden' }}>{s.text}</pre>
        {(s.text ?? '').length > 400 && (
          <button type="button" onClick={() => setExpanded(!expanded)} style={{ fontSize: 12, color: BLUE }}>
            {expanded ? 'Show less' : 'Show all'}
          </button>
        )}
      </div>
    )
  }
  if (s.kind === 'action') {
    const tone = s.action_status === 'executed' ? OUTCOME_TONE.completed
      : s.action_status === 'refused' ? OUTCOME_TONE.refused
        : s.action_status === 'would' ? { fg: 'rgb(83,56,158)', bg: 'rgb(244,243,255)' } : OUTCOME_TONE.escalated
    return (
      <div className="flex items-start gap-8" style={{ marginBottom: 10, padding: '8px 10px', borderRadius: 8,
        backgroundColor: tone.bg }} data-step="action" data-status={s.action_status ?? ''}>
        <Chip text={ACTION_STATUS_LABEL[s.action_status ?? ''] ?? (s.action_status ?? 'Action')} fg={tone.fg} bg="#fff" />
        <div style={{ fontSize: 13, color: TEXT }}>
          <span style={{ fontWeight: 500 }}>{s.tool_name}</span>{s.text ? ' — ' + s.text : ''}
        </div>
      </div>
    )
  }
  return <div style={{ ...box, marginBottom: 10 }}>{s.text}</div>
}

function RunDetail({ runId, onClose }: { runId: number; onClose: () => void }) {
  const run = useQuery({ queryKey: ['ai-run', runId], queryFn: () => aiRun(runId) })
  const r = run.data
  return (
    <Modal title={`Run #${runId}`} subtitle={r ? `${r.agent_name}${r.version != null ? ` v${r.version}` : ''} · ${r.trigger_label} · ${stamp(r.created_at)}` : undefined}
      onClose={onClose} width={860}>
      {run.isError && <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>{(run.error as Error).message}</div>}
      {r && (
        <>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 10, marginBottom: 14 }}>
            <Stat label="Outcome" value={<Chip text={outcomeLabel(r.outcome)} {...(OUTCOME_TONE[r.outcome] ?? OUTCOME_TONE.queued)} />} />
            <Stat label="Mode" value={r.is_test ? 'Try-it test' : modeLabel(r.mode)} />
            <Stat label="Subject" value={r.subject || '—'} />
            <Stat label="Model" value={r.model ? `${r.model}` : '—'} />
            <Stat label="Tokens" value={r.provider ? formatTokens(r.tokens) : '—'} />
            <Stat label="Cost" value={r.provider ? formatCost(r.cost) : '—'} />
            <Stat label="Latency" value={formatLatency(r.latency_ms)} />
            <Stat label="Finished" value={stamp(r.finished_at)} />
          </div>
          {r.reason && (
            <div style={{ fontSize: 13, color: TEXT, padding: '8px 10px', borderRadius: 8, backgroundColor: PAGE_BG, marginBottom: 12 }}>
              {r.reason}
            </div>
          )}
          <div style={{ fontSize: 14, fontWeight: 600, color: INK, margin: '6px 0 10px' }}>Transcript</div>
          {r.steps.length === 0 && <div style={{ fontSize: 13, color: MUTED }}>No transcript — the agent did not call a model.</div>}
          {r.steps.map((s) => <StepView key={s.id} s={s} />)}
          {r.suggestions.length > 0 && (
            <>
              <div style={{ fontSize: 14, fontWeight: 600, color: INK, margin: '16px 0 8px' }}>Suggestions</div>
              {r.suggestions.map((s) => (
                <div key={s.id} className="flex items-center gap-8" style={{ padding: '6px 0', borderBottom: `1px solid ${LINE}`, fontSize: 13 }}>
                  <Chip text={s.status} fg="rgb(71,84,103)" bg="rgb(242,244,247)" />
                  <span>{s.summary}</span>
                </div>
              ))}
            </>
          )}
        </>
      )}
    </Modal>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ padding: '8px 10px', border: `1px solid ${LINE}`, borderRadius: 8 }}>
      <div style={{ fontSize: 11, color: FAINT }}>{label}</div>
      <div style={{ fontSize: 13, color: TEXT, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white" style={{ border: `1px solid ${LINE}`, borderRadius: 8, padding: 16 }}>
      <div style={{ fontSize: 16, fontWeight: 600, color: INK, marginBottom: 12 }}>{title}</div>
      {children}
    </div>
  )
}

function BarList({ rows, color = BLUE }: { rows: { label: string; pct: number; text: string }[]; color?: string }) {
  if (rows.length === 0) return <div style={{ fontSize: 13, color: MUTED }}>Nothing yet.</div>
  return (
    <div>
      {rows.map((b) => (
        <div key={b.label} className="flex items-center gap-12" style={{ marginBottom: 6 }}>
          <div style={{ width: 110, fontSize: 12, color: MUTED, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.label}</div>
          <div className="flex-1" style={{ height: 10, backgroundColor: 'rgb(242,244,247)', borderRadius: 5 }}>
            <div style={{ width: `${b.pct}%`, height: 10, borderRadius: 5, backgroundColor: color }} />
          </div>
          <div style={{ width: 70, fontSize: 12, color: TEXT, textAlign: 'right' }}>{b.text}</div>
        </div>
      ))}
    </div>
  )
}

function Metrics() {
  const [days, setDays] = useState(30)
  const m = useQuery({ queryKey: ['ai-metrics', days], queryFn: () => aiMetrics(days) })
  const d = m.data
  return (
    <div style={{ marginTop: 12 }}>
      <div className="flex items-center" style={{ gap: 12 }}>
        <select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}
          style={{ ...INPUT, marginTop: 0, height: 30, fontSize: 13, width: 'auto' }}>
          {[7, 30, 90].map((n) => <option key={n} value={n}>Last {n} days</option>)}
        </select>
        <span style={{ fontSize: 12, color: FAINT }}>Try-it tests are not counted. Days are Eastern time.</span>
      </div>
      {m.isError && <div role="alert" style={{ marginTop: 10, fontSize: 13, color: 'rgb(180,35,24)' }}>{(m.error as Error).message}</div>}
      {d && (
        <>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12, marginTop: 12 }}>
            <Card title="Runs"><div style={{ fontSize: 30, fontWeight: 500, color: INK }} data-metric="runs">{d.runs}</div></Card>
            <Card title="Cost"><div style={{ fontSize: 30, fontWeight: 500, color: INK }}>{formatCost(d.total_cost)}</div></Card>
            <Card title="Average latency"><div style={{ fontSize: 30, fontWeight: 500, color: INK }}>{formatLatency(d.avg_latency_ms)}</div></Card>
            <Card title="Open knowledge gaps"><div style={{ fontSize: 30, fontWeight: 500, color: INK }}>{d.open_knowledge_gaps}</div></Card>
          </div>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12, marginTop: 12 }}>
            <Card title="Runs per day">
              <BarList rows={bars(d.per_day, (r) => r.day.slice(5), (r) => r.runs)} />
            </Card>
            <Card title="Outcomes">
              <BarList rows={bars(outcomeRows(d.outcomes), (r) => r.label, (r) => r.count)} color="rgb(18,183,106)" />
            </Card>
            <Card title="Cost per day">
              <BarList rows={bars(d.per_day, (r) => r.day.slice(5), (r) => Number(r.cost ?? 0),
                (r) => formatCost(r.cost) + (r.cost_unknown_runs ? ` +${r.cost_unknown_runs}?` : ''))} color="rgb(247,144,9)" />
            </Card>
            <Card title="Cost per agent">
              <BarList rows={bars(d.per_agent, (r) => r.agent_name, (r) => Number(r.cost ?? 0),
                (r) => formatCost(r.cost))} color="rgb(122,90,248)" />
            </Card>
            <Card title="Top actions">
              {d.top_actions.length === 0 ? <div style={{ fontSize: 13, color: MUTED }}>Nothing yet.</div> : (
                <table className="w-full" style={{ borderCollapse: 'collapse' }}>
                  <thead><tr>
                    <Th>Action</Th><Th width={80} align="right">Done</Th><Th width={90} align="right">Suggested</Th><Th width={80} align="right">Refused</Th>
                  </tr></thead>
                  <tbody>
                    {d.top_actions.map((a) => (
                      <tr key={a.action}><Td>{a.action}</Td><Td align="right">{a.executed}</Td><Td align="right">{a.suggested}</Td><Td align="right">{a.refused}</Td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
            <Card title="Runs per agent">
              <BarList rows={bars(d.per_agent, (r) => r.agent_name, (r) => r.runs)} />
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
