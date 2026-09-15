import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  aiAgents, aiSuggestions, approveAiSuggestion, dismissAiSuggestion, runAiAgent,
} from '../lib/api'
import type { Me } from '../lib/auth'
import { canOpenAiAgents, modeLabel } from '../lib/aiAgents'
import { IconSparkle } from './Icon'

const TEXT = 'rgb(16,24,40)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'
const BLUE = 'rgb(21,94,239)'

/** "AI: <agent name>" beside a thread event an agent wrote (2026-09-15). Nothing otherwise. */
export function AiAuthorChip({ author }: { author: string | null | undefined }) {
  if (!author) return null
  return (
    <span title="Written by an AI agent" style={{ marginLeft: 6, padding: '1px 6px', borderRadius: 10,
      fontSize: 11, color: 'rgb(83,56,158)', backgroundColor: 'rgb(244,243,255)', whiteSpace: 'nowrap' }}>
      {author}
    </span>
  )
}

/**
 * "Run AI agent" and the pending suggestions for one contact or one opportunity
 * (2026-09-15). Drawn only for a user who can open AI Agents. The suggestion list appears
 * only when there is something to decide; "Run AI agent" only when an agent that is on and
 * has the Manual trigger exists — never a control that cannot work.
 */
export function AiAgentPanel({ user, contactId, opportunityId, compact = false }: {
  user: Me; contactId?: number; opportunityId?: number; compact?: boolean
}) {
  const allowed = canOpenAiAgents(user)
  const qc = useQueryClient()
  const agents = useQuery({ queryKey: ['ai-agents'], queryFn: aiAgents, enabled: allowed })
  const key = ['ai-suggestions', contactId ?? null, opportunityId ?? null]
  const suggestions = useQuery({
    queryKey: key, enabled: allowed, refetchInterval: 30000,
    queryFn: () => aiSuggestions(opportunityId != null ? { opportunity_id: opportunityId } : { contact_id: contactId }),
  })
  const [agentId, setAgentId] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const runnable = (agents.data ?? []).filter((a) => a.mode !== 'off' && a.published_version != null
    && (a.published_triggers ?? []).includes('manual'))
  const run = useMutation({
    mutationFn: () => runAiAgent(Number(agentId), { contact_id: contactId ?? null, opportunity_id: opportunityId ?? null }),
    onSuccess: (r) => setMessage({ ok: r.outcome !== 'skipped',
      text: r.outcome === 'skipped' ? `Not run: ${r.reason}` : `Queued (run #${r.run_id}, ${modeLabel(r.mode)}). It appears in Agent Logs.` }),
    onError: (e: Error) => setMessage({ ok: false, text: e.message }),
  })
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: number; approve: boolean }) =>
      (approve ? approveAiSuggestion(id) : dismissAiSuggestion(id)),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ['ai-suggestions'] })
      setMessage(s.status === 'failed'
        ? { ok: false, text: `Could not carry it out: ${String((s.result as { refused?: string } | null)?.refused ?? '')}` }
        : { ok: true, text: s.status === 'approved' ? 'Done.' : 'Dismissed.' })
    },
    onError: (e: Error) => setMessage({ ok: false, text: e.message }),
  })

  if (!allowed) return null
  const pending = suggestions.data ?? []
  if (compact && runnable.length === 0 && pending.length === 0) return null

  return (
    <div data-ai-panel style={{ marginTop: compact ? 20 : 0 }}>
      <div className="flex items-center gap-8" style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>
        <IconSparkle size={16} color={BLUE} /> AI agent
      </div>
      {runnable.length > 0 ? (
        <div className="flex items-center gap-8" style={{ marginTop: 8 }}>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)} aria-label="AI agent to run"
            style={{ flex: 1, height: 32, fontSize: 13, borderRadius: 6, border: '1px solid rgb(208,213,221)',
              padding: '0 8px', backgroundColor: '#fff', color: TEXT }}>
            <option value="">Choose an agent</option>
            {runnable.map((a) => <option key={a.id} value={a.id}>{a.name} ({modeLabel(a.mode)})</option>)}
          </select>
          <button type="button" disabled={!agentId || run.isPending} onClick={() => { setMessage(null); run.mutate() }}
            style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, fontWeight: 500, color: '#fff',
              backgroundColor: BLUE, opacity: !agentId || run.isPending ? 0.5 : 1 }}>
            Run AI agent
          </button>
        </div>
      ) : !compact && (
        <div style={{ fontSize: 12, color: MUTED, marginTop: 6 }}>
          No agent can be run by hand: an agent needs the Manual trigger, a published version and Suggest or Auto-pilot mode.
        </div>
      )}
      {message && (
        <div role={message.ok ? 'status' : 'alert'} style={{ fontSize: 12, marginTop: 6,
          color: message.ok ? 'rgb(2,122,72)' : 'rgb(180,35,24)' }}>{message.text}</div>
      )}
      {pending.length > 0 && (
        <div style={{ marginTop: 12 }} aria-label="AI suggestions">
          <div style={{ fontSize: 12, fontWeight: 500, color: MUTED }}>Waiting for approval ({pending.length})</div>
          {pending.map((s) => (
            <div key={s.id} data-suggestion={s.id} style={{ marginTop: 8, padding: 10, borderRadius: 8,
              border: `1px solid ${LINE}`, backgroundColor: 'rgb(255,250,235)' }}>
              <div style={{ fontSize: 13, color: TEXT }}>{s.summary}</div>
              <div style={{ fontSize: 11, color: MUTED, marginTop: 4 }}>
                AI: {s.agent_name ?? 'agent'} · run #{s.run_id}
              </div>
              <div className="flex gap-8" style={{ marginTop: 8 }}>
                <button type="button" disabled={decide.isPending} onClick={() => decide.mutate({ id: s.id, approve: true })}
                  style={{ height: 28, padding: '0 10px', borderRadius: 6, fontSize: 12, fontWeight: 500, color: '#fff', backgroundColor: BLUE }}>
                  Approve
                </button>
                <button type="button" disabled={decide.isPending} onClick={() => decide.mutate({ id: s.id, approve: false })}
                  style={{ height: 28, padding: '0 10px', borderRadius: 6, fontSize: 12, fontWeight: 500, color: TEXT,
                    backgroundColor: '#fff', border: '1px solid rgb(208,213,221)' }}>
                  Dismiss
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
