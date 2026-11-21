import type { OwenAiChangeSet } from '../../lib/api'
import { changeMark, changeSetHeadline } from '../../lib/owenAi'
import { BLUE, INK, LINE, MUTED, RED } from './tokens'

/**
 * A Zuper change list Owen AI proposed (2026-11-21, app/owen_ai/changes.py), drawn under the
 * answer that listed it. There is no button on purpose: Owen says "do it" (or which numbers) in
 * the chat, and the server makes the changes only for a later message he typed in this chat.
 * After that each line shows what happened — Done, Failed, Skipped (it changed in Zuper since).
 */
export function ChangeSetCard({ set }: { set: OwenAiChangeSet }) {
  const pending = set.status === 'pending'
  const closed = set.status === 'superseded' || set.status === 'expired'
  return (
    <section aria-label={`Zuper change list #${set.id}`}
      style={{ margin: '6px 0 18px', border: `1px solid ${pending ? 'rgb(178,204,255)' : LINE}`, borderRadius: 12,
        background: pending ? 'rgb(245,248,255)' : '#fff', opacity: closed ? 0.6 : 1, overflow: 'hidden' }}>
      <header style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', padding: '10px 14px',
        borderBottom: `1px solid ${LINE}` }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: INK }}>Zuper changes · list #{set.id}</span>
        <span style={{ fontSize: 13, color: pending ? BLUE : MUTED }}>{changeSetHeadline(set.status)}</span>
      </header>
      <ol style={{ listStyle: 'none', margin: 0, padding: '4px 0' }}>
        {set.changes.map((c) => {
          const m = changeMark(c.status)
          const color = m.tone === 'ok' ? 'rgb(6,118,71)' : m.tone === 'bad' ? RED : MUTED
          return (
            <li key={c.n} style={{ display: 'flex', gap: 10, padding: '8px 14px', fontSize: 13, lineHeight: '19px' }}>
              <span style={{ color: MUTED, minWidth: 18, textAlign: 'right' }}>{c.n}.</span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ color: INK, wordBreak: 'break-word' }}>
                  {c.job_number && (c.job_url
                    ? <a href={c.job_url} target="_blank" rel="noreferrer" style={{ color: BLUE }}>#{c.job_number}</a>
                    : <span>#{c.job_number}</span>)}
                  {c.customer_name && <span> {c.customer_name}</span>}
                  {(c.job_number || c.customer_name) && ' — '}{c.title}
                </div>
                {c.warning && <div style={{ color: 'rgb(181,71,8)', fontSize: 12 }}>⚠ {c.warning}</div>}
                {c.evidence && <div style={{ color: MUTED, fontSize: 12 }}>Because: {c.evidence}</div>}
                {c.status !== 'pending' && (
                  <div style={{ color, fontSize: 12, marginTop: 2 }}>
                    <span aria-hidden="true">{m.mark}</span> {m.label}{c.result ? ` — ${c.result}` : ''}</div>
                )}
              </div>
            </li>
          )
        })}
      </ol>
      {set.owen_said && (
        <footer style={{ padding: '6px 14px 10px', fontSize: 12, color: MUTED }}>You said: “{set.owen_said}”</footer>
      )}
    </section>
  )
}
