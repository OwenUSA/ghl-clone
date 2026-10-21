import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { registryActivity, registryOverview } from '../../lib/api'
import type { Me } from '../../lib/auth'
import { canOpenAiAgents } from '../../lib/aiAgents'
import { Empty, FAINT, INK, INPUT, LINE, MUTED, PageHeader, TableCard, Td, Th } from './aiUi'

const TONE: Record<string, string> = {
  good: 'rgb(2,122,72)', bad: 'rgb(180,35,24)', info: INK, muted: MUTED,
}

/**
 * Activity (2026-10-21): what every agent and automation did, newest first, read from each
 * one's own log — texts sent, shadow replies, failures, Zuper changes, level changes, alerts.
 */
export function ActivityTab({ user }: { user: Me }) {
  const [key, setKey] = useState('')
  const overview = useQuery({ queryKey: ['registry'], queryFn: registryOverview })
  const q = useQuery({ queryKey: ['registry-activity', key], queryFn: () => registryActivity(key || null),
    refetchInterval: 20000 })
  if (!canOpenAiAgents(user)) return null
  const items = q.data?.items ?? []
  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 24px' }}>
      <PageHeader title="Activity"
        subtitle="What each agent and automation did, newest first: texts, shadow replies, failures, Zuper changes and every setting a person changed." />
      <TableCard toolbar={
        <select id="registry-activity-entry" aria-label="Agent or automation" value={key}
          onChange={(e) => setKey(e.target.value)}
          style={{ ...INPUT, marginTop: 0, height: 30, fontSize: 13, width: 'auto' }}>
          <option value="">Everything</option>
          {(overview.data?.entries ?? []).map((e) => <option key={e.key} value={e.key}>{e.name}</option>)}
        </select>
      }>
        {q.isLoading ? <Empty>Loading…</Empty> : items.length === 0 ? (
          <Empty>Nothing yet.</Empty>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
            <thead><tr><Th width={170}>When</Th><Th width={200}>Who</Th><Th>What happened</Th></tr></thead>
            <tbody>
              {items.map((it, i) => (
                <tr key={i}>
                  <Td style={{ color: FAINT, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                    {it.at ? new Date(it.at).toLocaleString() : '—'}
                  </Td>
                  <Td>{it.name}</Td>
                  <Td style={{ color: TONE[it.tone] ?? INK, whiteSpace: 'pre-wrap',
                    borderBottom: `1px solid ${LINE}` }}>{it.text}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </TableCard>
    </div>
  )
}
