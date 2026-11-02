import type { OwenAiStatus } from '../../lib/api'
import { LINE, MUTED } from './tokens'

/** The strip above the chat: the server's sentence when Owen AI cannot answer, and what it can reach. */
export function StatusStrip({ status }: { status: OwenAiStatus | undefined }) {
  if (!status) return null
  return (
    <div style={{ background: '#fff', borderBottom: `1px solid ${LINE}` }}>
      {status.sentence && (
        <div role="alert" style={{ padding: '8px 16px', fontSize: 13, color: 'rgb(181,71,8)',
          background: 'rgb(255,250,235)', borderBottom: '1px solid rgb(254,223,137)' }}>
          {status.sentence}</div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, padding: '6px 16px',
        fontSize: 12, color: MUTED }}>
        <Badge on={status.zuper} label="Zuper" />
        <Badge on={status.quo} label="Quo" />
        <Badge on={status.claude_login} label="Claude login" />
        {status.model && <span style={{ marginLeft: 4 }}>Model: {status.model}</span>}
      </div>
    </div>
  )
}

function Badge({ on, label }: { on: boolean; label: string }) {
  return (
    <span title={`${label}: ${on ? 'connected' : 'not connected'}`}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '1px 8px', borderRadius: 10,
        fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap',
        color: on ? 'rgb(2,122,72)' : 'rgb(71,84,103)', background: on ? 'rgb(236,253,243)' : 'rgb(242,244,247)' }}>
      <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: 3,
        background: on ? 'rgb(18,183,106)' : 'rgb(152,162,179)' }} />
      {label}
    </span>
  )
}
