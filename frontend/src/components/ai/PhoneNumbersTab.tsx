import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  aiAgentSpend, aiPhoneNumbers, ApiError, assignAiPhoneNumber, setAiAgentSpend, unassignAiPhoneNumber,
  type AssignMode, type PhoneNumberRow, type PhoneNumbers,
} from '../../lib/api'
import type { Me } from '../../lib/auth'
import { DAYS, dayLabel, hoursBody, hoursProblems, hoursSummary, isAiAdmin, spendLine, type HoursRanges } from '../../lib/aiAgents'
import { formatPhone } from '../../lib/phone'
import { IconClose } from '../PipelineIcons'
import {
  BLUE, Confirm, Empty, Field, INPUT, MUTED, Modal, Notice, PageHeader, primarySmall, smallButton, TableCard, Td, TEXT, Th,
} from './aiUi'

/**
 * AI Agents → Phone numbers (2026-10-06, docs/RETELL-PLAN.md C5 / C6, decisions 6, 11, 18).
 *
 * Which of the phone system's numbers a voice agent answers, and how: AI first, office first then
 * AI, or AI outside office hours (hours in Eastern time, never assumed). owen-main builds the flow;
 * a number whose flow was built by hand is only replaced after a second, explicit confirmation
 * (owen-main's 409). Removing the assignment puts the previous flow back. Plus the agents' daily
 * spend cap. ADMIN only — the server refuses everyone else; an unlinked server says so and asks
 * nothing.
 */
export function PhoneNumbersTab({ user }: { user: Me }) {
  const numbers = useQuery({ queryKey: ['ai-phone-numbers'], queryFn: aiPhoneNumbers, enabled: isAiAdmin(user) })
  const [editing, setEditing] = useState<PhoneNumberRow | null>(null)
  const [removing, setRemoving] = useState<PhoneNumberRow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const qc = useQueryClient()
  const remove = useMutation({
    mutationFn: (id: string) => unassignAiPhoneNumber(id),
    onSuccess: () => { setRemoving(null); setNotice('Assignment removed — the number has its previous flow back.')
      qc.invalidateQueries({ queryKey: ['ai-phone-numbers'] }) },
    onError: (e: Error) => { setRemoving(null); setError(e.message) },
  })
  if (!isAiAdmin(user)) return null
  const data = numbers.data
  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 16px' }}>
      <PageHeader title="Phone numbers"
        subtitle="Which voice agent answers which number, and how. The phone system builds the call flow for it." />
      <Notice error={error} notice={notice} onDismiss={() => { setError(null); setNotice(null) }} />
      <SpendCard />
      {data?.detail && (
        <div role="status" style={{ marginTop: 12, fontSize: 13, color: MUTED }}>{data.detail}</div>
      )}
      {numbers.isError && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>
        {(numbers.error as Error).message}</div>}
      <TableCard>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 760 }}>
          <thead><tr>
            <Th width={200}>Number</Th><Th>Answered by</Th><Th width={260}>How</Th><Th width={190} align="center">Actions</Th>
          </tr></thead>
          <tbody>
            {(data?.numbers ?? []).map((n) => (
              <tr key={n.id}>
                <Td>
                  <div style={{ fontWeight: 500 }}>{n.e164 ? formatPhone(n.e164) : n.id}</div>
                  {n.label && <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{n.label}</div>}
                </Td>
                <Td>
                  {n.assignment ? (
                    <span>{n.assignment.crm_agent_name ?? n.assignment.agent_name}
                      {!n.assignment.crm_agent_id && <span style={{ fontSize: 12, color: MUTED }}> (not a CRM agent)</span>}</span>
                  ) : n.assignable ? <span style={{ color: MUTED }}>Its own flow — no AI agent</span>
                    : <span style={{ color: MUTED }}>Cannot be assigned{n.reason ? `: ${n.reason}` : ''}</span>}
                </Td>
                <Td>
                  {n.assignment ? (
                    <>
                      <div>{n.assignment.mode_label}</div>
                      {n.assignment.hours && <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
                        Office hours: {hoursSummary(n.assignment.hours)}</div>}
                    </>
                  ) : '—'}
                </Td>
                <Td align="center">
                  {n.assignable && (
                    <button type="button" onClick={() => setEditing(n)} style={{ fontSize: 13, color: BLUE, fontWeight: 500, marginRight: 14 }}>
                      {n.assignment ? 'Change' : 'Assign agent'}
                    </button>
                  )}
                  {n.assignment && (
                    <button type="button" onClick={() => setRemoving(n)} style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>
                      Remove
                    </button>
                  )}
                </Td>
              </tr>
            ))}
            {numbers.isSuccess && data?.configured && data.numbers.length === 0 && !data.detail && (
              <tr><td colSpan={4}><Empty>The phone system lists no numbers.</Empty></td></tr>
            )}
          </tbody>
        </table>
      </TableCard>
      {editing && data && (
        <AssignDialog number={editing} data={data} onClose={() => setEditing(null)}
          onDone={(msg) => { setEditing(null); setNotice(msg); qc.invalidateQueries({ queryKey: ['ai-phone-numbers'] }) }} />
      )}
      {removing && (
        <Confirm title="Remove the AI agent from this number?" confirmLabel="Remove" danger busy={remove.isPending}
          body={<>Calls to {removing.e164 ? formatPhone(removing.e164) : removing.id} go back to the flow the number had before
            the agent was assigned.</>}
          onCancel={() => setRemoving(null)} onConfirm={() => remove.mutate(removing.id)} />
      )}
    </div>
  )
}

function emptyDays(): HoursRanges {
  return Object.fromEntries(DAYS.map((d) => [d, []])) as HoursRanges
}

function AssignDialog({ number, data, onClose, onDone }: {
  number: PhoneNumberRow; data: PhoneNumbers; onClose: () => void; onDone: (msg: string) => void
}) {
  const a = number.assignment
  const [agentId, setAgentId] = useState<number | ''>(a?.crm_agent_id ?? data.agents[0]?.id ?? '')
  const [mode, setMode] = useState<AssignMode>(a?.mode ?? 'ai_first')
  const [days, setDays] = useState<HoursRanges>(() => ({ ...emptyDays(), ...(a?.hours?.days ?? {}) }))
  const [error, setError] = useState<string | null>(null)
  const [replaceAsk, setReplaceAsk] = useState<string | null>(null)
  const needsHours = mode === 'after_hours_ai'
  const problems = needsHours ? hoursProblems(days) : []
  const save = useMutation({
    mutationFn: (replace: boolean) => assignAiPhoneNumber(number.id, {
      agent_id: Number(agentId), mode, hours: needsHours ? hoursBody(days) : null, replace }),
    onSuccess: () => onDone('Assigned. The phone system has built the flow for this number.'),
    onError: (e: Error) => {
      if (e instanceof ApiError && e.status === 409) { setError(null); setReplaceAsk(e.message) } else { setReplaceAsk(null); setError(e.message) }
    },
  })
  const setRange = (d: string, i: number, j: 0 | 1, v: string) =>
    setDays((x) => ({ ...x, [d]: (x[d] ?? []).map((r, k) => (k === i ? (j === 0 ? [v, r[1]] : [r[0], v]) : r)) as [string, string][] }))
  return (
    <Modal title={`Assign an agent to ${number.e164 ? formatPhone(number.e164) : number.id}`} onClose={onClose} width={620}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" disabled={agentId === '' || problems.length > 0 || save.isPending}
          onClick={() => save.mutate(false)} style={{ ...primarySmall, opacity: agentId === '' || problems.length ? 0.5 : 1 }}>
          {save.isPending ? 'Saving…' : 'Assign'}
        </button>
      </>}>
      {data.agents.length === 0 ? (
        <div style={{ fontSize: 13, color: MUTED }}>No voice agent is published yet. Publish one on the Agents tab first.</div>
      ) : (
        <>
          <Field label="Voice agent" required hint="Only published voice agents — the phone system only knows a published agent.">
            <select value={agentId} aria-label="Voice agent" style={INPUT}
              onChange={(e) => setAgentId(e.target.value ? Number(e.target.value) : '')}>
              {data.agents.map((x) => <option key={x.id} value={x.id}>{x.name}{x.engine === 'retell' ? ' (Retell)' : ''}</option>)}
            </select>
          </Field>
          <Field label="How calls reach it" required>
            <select value={mode} aria-label="How calls reach it" style={INPUT} onChange={(e) => setMode(e.target.value as AssignMode)}>
              {data.modes.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </Field>
          {needsHours && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 500, color: TEXT }}>Office hours (Eastern time)</div>
              <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>The office answers in these hours; the agent outside them.
                Nothing is filled in for you.</div>
              {DAYS.map((d) => (
                <div key={d} className="flex flex-wrap items-center gap-8" style={{ marginTop: 8 }}>
                  <span style={{ width: 40, fontSize: 13, color: TEXT }}>{dayLabel(d)}</span>
                  {(days[d] ?? []).length === 0 && <span style={{ fontSize: 12, color: MUTED }}>Closed</span>}
                  {(days[d] ?? []).map((r, i) => (
                    <span key={i} className="flex items-center gap-4">
                      <input type="time" value={r[0]} aria-label={`${dayLabel(d)} opens`} style={{ ...INPUT, width: 120 }}
                        onChange={(e) => setRange(d, i, 0, e.target.value)} />
                      –
                      <input type="time" value={r[1]} aria-label={`${dayLabel(d)} closes`} style={{ ...INPUT, width: 120 }}
                        onChange={(e) => setRange(d, i, 1, e.target.value)} />
                      <button type="button" aria-label={`Remove ${dayLabel(d)} range`} style={{ fontSize: 12, color: MUTED }}
                        onClick={() => setDays((x) => ({ ...x, [d]: (x[d] ?? []).filter((_, k) => k !== i) }))}><IconClose size={14} color={MUTED} /></button>
                    </span>
                  ))}
                  {(days[d] ?? []).length < 4 && (
                    <button type="button" style={{ fontSize: 12, color: BLUE }}
                      onClick={() => setDays((x) => ({ ...x, [d]: [...(x[d] ?? []), ['', ''] as [string, string]] }))}>
                      + Add hours
                    </button>
                  )}
                </div>
              ))}
              {problems.length > 0 && <div style={{ marginTop: 8, fontSize: 12, color: 'rgb(180,35,24)' }}>{problems.join(' ')}</div>}
            </div>
          )}
        </>
      )}
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
      {replaceAsk && (
        <Confirm title="Replace this number's hand-built flow?" confirmLabel="Replace it" danger busy={save.isPending}
          body={<>{replaceAsk} The phone system keeps the old flow, and removing the assignment later puts it back.</>}
          onCancel={() => setReplaceAsk(null)} onConfirm={() => { setReplaceAsk(null); save.mutate(true) }} />
      )}
    </Modal>
  )
}

/** The agents' daily spend against the cap (decision 11: pilot $25/day, alert at 80%). */
function SpendCard() {
  const qc = useQueryClient()
  const spend = useQuery({ queryKey: ['ai-agent-spend'], queryFn: aiAgentSpend, refetchInterval: 60_000 })
  const [cap, setCap] = useState<string | null>(null)
  const [pct, setPct] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => setAiAgentSpend(Number(cap ?? spend.data?.daily_cap_usd), Number(pct ?? spend.data?.alert_pct)),
    onSuccess: (s) => { setCap(null); setPct(null); setError(null); qc.setQueryData(['ai-agent-spend'], s) },
    onError: (e: Error) => setError(e.message),
  })
  const s = spend.data
  const capValue = cap ?? (s?.daily_cap_usd != null ? String(s.daily_cap_usd) : '')
  const pctValue = pct ?? (s?.alert_pct != null ? String(s.alert_pct) : '')
  const valid = capValue !== '' && Number(capValue) >= 0 && pctValue !== '' && Number(pctValue) >= 1 && Number(pctValue) <= 100
  return (
    <div className="bg-white" style={{ marginTop: 12, border: '1px solid rgb(234,236,240)', borderRadius: 4, padding: '12px 14px' }}>
      <div className="flex flex-wrap items-center gap-12">
        <div style={{ fontSize: 14, fontWeight: 600, color: TEXT }}>Voice agents' spend</div>
        {s && s.configured && !s.detail && (
          <span style={{ fontSize: 13, color: s.over_alert ? 'rgb(180,35,24)' : MUTED }}>
            {spendLine(s)}{s.over_alert ? ' — past the alert level' : ''}
          </span>
        )}
      </div>
      {s?.detail && <div role="status" style={{ marginTop: 6, fontSize: 13, color: MUTED }}>{s.detail}</div>}
      {s?.configured && !s.detail && (
        <div className="flex flex-wrap items-end gap-12" style={{ marginTop: 4 }}>
          <Field label="Daily cap (US$)" hint="Counted by the phone system with Retell's real cost. Over it, calls go to the fallback.">
            <input type="number" min={0} step="0.01" value={capValue} aria-label="Daily cap" style={{ ...INPUT, width: 140 }}
              onChange={(e) => setCap(e.target.value)} />
          </Field>
          <Field label="Alert at (%)">
            <input type="number" min={1} max={100} value={pctValue} aria-label="Alert at" style={{ ...INPUT, width: 100 }}
              onChange={(e) => setPct(e.target.value)} />
          </Field>
          <button type="button" disabled={!valid || save.isPending || (cap == null && pct == null)}
            onClick={() => save.mutate()} style={{ ...primarySmall, marginBottom: 2, opacity: !valid || (cap == null && pct == null) ? 0.5 : 1 }}>
            Save
          </button>
        </div>
      )}
      {error && <div role="alert" style={{ marginTop: 8, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </div>
  )
}
