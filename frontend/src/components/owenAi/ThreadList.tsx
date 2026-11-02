import { useState } from 'react'
import type { OwenAiThread } from '../../lib/api'
import { groupThreads, jobNumber } from '../../lib/owenAi'
import { BLUE, INK, LINE, MUTED, RED, SIDEBAR_BG, chipBtn } from './tokens'

/**
 * The left column: Today / Job… / New chat, then every open conversation grouped Daily / Jobs /
 * Other, newest first. On a narrow screen it is a drawer over the chat (like the Dispatch
 * assistant's chat list).
 */
export function ThreadList({ threads, loading, error, activeId, onOpen, onToday, onJob, onNew, onArchive,
  opening, problem, narrow, onClose }: {
  threads: OwenAiThread[]; loading: boolean; error: string | null; activeId: number | null
  onOpen: (id: number) => void; onToday: () => void; onJob: (jobNumber: string) => void; onNew: () => void
  onArchive: (id: number) => void; opening: boolean; problem: string | null
  narrow: boolean; onClose: () => void
}) {
  const [jobOpen, setJobOpen] = useState(false)
  const [job, setJob] = useState('')
  const submitJob = () => {
    const n = jobNumber(job)
    if (!n) return
    onJob(n)
    setJob('')
    setJobOpen(false)
  }
  const groups = groupThreads(threads)
  return (
    <aside aria-label="Owen AI conversations" style={{ width: 260, flexShrink: 0, background: SIDEBAR_BG,
      borderRight: `1px solid ${LINE}`, display: 'flex', flexDirection: 'column', minHeight: 0,
      ...(narrow ? { position: 'absolute', top: 0, bottom: 0, left: 0, zIndex: 20,
        boxShadow: '4px 0 20px rgba(16,24,40,0.12)' } : {}) }}>
      <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {narrow && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: INK }}>Conversations</span>
            <button type="button" onClick={onClose} aria-label="Close conversations"
              style={{ ...chipBtn, borderRadius: 10 }}>Close</button>
          </div>
        )}
        <div style={{ display: 'flex', gap: 6 }}>
          <button type="button" onClick={onToday} disabled={opening} className="hover:bg-[rgb(249,250,251)]"
            style={startBtn}>Today</button>
          <button type="button" onClick={() => setJobOpen(!jobOpen)} disabled={opening}
            aria-expanded={jobOpen} className="hover:bg-[rgb(249,250,251)]" style={startBtn}>Job…</button>
          <button type="button" onClick={onNew} disabled={opening} className="hover:bg-[rgb(249,250,251)]"
            style={startBtn}>New chat</button>
        </div>
        {jobOpen && (
          <form onSubmit={(e) => { e.preventDefault(); submitJob() }} style={{ display: 'flex', gap: 6 }}>
            <input autoFocus value={job} onChange={(e) => setJob(e.target.value)} placeholder="Job number, e.g. 1234"
              aria-label="Job number" inputMode="numeric" maxLength={41}
              onKeyDown={(e) => { if (e.key === 'Escape') setJobOpen(false) }}
              style={{ flex: 1, minWidth: 0, fontSize: 13, color: INK, border: `1px solid ${BLUE}`, borderRadius: 8,
                padding: '6px 8px', outline: 'none', background: '#fff' }} />
            <button type="submit" disabled={!jobNumber(job) || opening}
              style={{ fontSize: 12, color: '#fff', background: jobNumber(job) ? BLUE : 'rgb(152,162,179)',
                borderRadius: 8, padding: '0 10px' }}>Open</button>
          </form>
        )}
        {problem && <div role="alert" style={{ color: RED, fontSize: 12 }}>{problem}</div>}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        {loading && <div style={{ color: MUTED, fontSize: 12, padding: '4px 8px' }}>Loading…</div>}
        {error && <div style={{ color: RED, fontSize: 12, padding: '4px 8px' }}>Could not load conversations: {error}</div>}
        {!loading && !error && threads.length === 0 && (
          <div style={{ color: MUTED, fontSize: 12, padding: '4px 8px' }}>Your conversations will appear here.</div>
        )}
        {groups.map((g) => (
          <div key={g.group} style={{ marginTop: 10 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: MUTED, padding: '0 8px 4px',
              textTransform: 'uppercase', letterSpacing: 0.4 }}>{g.group}</div>
            {g.threads.map((t) => {
              const active = t.id === activeId
              return (
                <div key={t.id} className="group" style={{ position: 'relative', margin: '1px 0' }}>
                  <button type="button" onClick={() => onOpen(t.id)} title={t.title}
                    aria-current={active ? 'true' : undefined}
                    className={active ? '' : 'hover:bg-[rgb(234,236,240)]'}
                    style={{ width: '100%', textAlign: 'left', borderRadius: 8, padding: '7px 30px 7px 10px',
                      background: active ? 'rgb(228,231,236)' : 'transparent', display: 'flex',
                      alignItems: 'center', gap: 6 }}>
                    {t.busy && <span className="owen-ai-pulse" aria-label="Working" title="Owen AI is working"
                      style={{ width: 7, height: 7, borderRadius: 4, background: BLUE, flexShrink: 0 }} />}
                    <span style={{ fontSize: 13, color: INK, fontWeight: active ? 500 : 400, minWidth: 0,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t.title || 'New chat'}</span>
                  </button>
                  <button type="button" aria-label={`Archive ${t.title}`} title="Archive"
                    onClick={(e) => { e.stopPropagation(); onArchive(t.id) }}
                    className={active ? '' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'}
                    style={{ position: 'absolute', right: 4, top: 5, width: 24, height: 24, borderRadius: 6,
                      color: MUTED, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
                      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <rect x="3" y="4" width="18" height="5" rx="1" /><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" />
                    </svg>
                  </button>
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </aside>
  )
}

const startBtn: React.CSSProperties = { flex: 1, textAlign: 'center', fontSize: 13, fontWeight: 500, color: INK,
  border: `1px solid ${LINE}`, borderRadius: 10, padding: '7px 6px', background: '#fff', whiteSpace: 'nowrap' }
