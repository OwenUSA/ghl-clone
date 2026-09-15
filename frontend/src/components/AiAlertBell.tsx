import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { aiAlerts, readAiAlert, readAllAiAlerts, type AiAlert } from '../lib/api'
import type { Me } from '../lib/auth'
import { canOpenAiAgents, stamp } from '../lib/aiAgents'
import { canOpenRecords, openRecord } from '../lib/openRecord'

const TEXT = 'rgb(16,24,40)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'

/**
 * The in-app alert bell (2026-09-15). This CRM had no notification mechanism, so this is a
 * minimal one: AI escalations write an alert per escalation user, and the bell shows them.
 * Pinned beside the status dot (StatusIndicator, top: 9, right: 16), one 36px step to its
 * left, on every page — and only for a user who can open AI Agents.
 */
export function AiAlertBell({ user }: { user: Me }) {
  const allowed = canOpenAiAgents(user)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const alerts = useQuery({ queryKey: ['ai-alerts'], queryFn: aiAlerts, enabled: allowed,
    refetchInterval: 60000, refetchOnWindowFocus: true })
  const read = useMutation({ mutationFn: readAiAlert, onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-alerts'] }) })
  const readAll = useMutation({ mutationFn: readAllAiAlerts, onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-alerts'] }) })

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  if (!allowed) return null
  const unread = alerts.data?.unread ?? 0
  const items = alerts.data?.items ?? []

  const go = (a: AiAlert) => {
    if (!a.read) read.mutate(a.id)
    if (!canOpenRecords()) return
    setOpen(false)
    if (a.opportunity_id != null) openRecord('opportunities', a.opportunity_id)
    else if (a.contact_id != null) openRecord('contacts', a.contact_id)
  }

  return (
    <div ref={ref} style={{ position: 'fixed', top: 9, right: 52, zIndex: 70 }}>
      <button type="button" aria-label={unread ? `Alerts: ${unread} unread` : 'Alerts'} aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{ position: 'relative', width: 32, height: 32, borderRadius: 999, display: 'flex',
          alignItems: 'center', justifyContent: 'center', color: 'rgb(71,84,103)',
          background: open ? 'rgb(242,244,247)' : 'transparent' }}>
        <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unread > 0 && (
          <span aria-hidden style={{ position: 'absolute', top: 2, right: 1, minWidth: 16, height: 16, padding: '0 4px',
            borderRadius: 8, fontSize: 10, fontWeight: 600, lineHeight: '16px', color: '#fff',
            backgroundColor: 'rgb(240,68,56)', boxShadow: '0 0 0 2px #fff' }}>
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div role="dialog" aria-label="Alerts" style={{ position: 'absolute', top: 38, right: 0, width: 340,
          maxWidth: 'calc(100vw - 32px)', background: '#fff', border: `1px solid ${LINE}`, borderRadius: 8,
          boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)' }}>
          <div className="flex items-center" style={{ padding: '10px 14px', borderBottom: `1px solid ${LINE}` }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: TEXT }}>Alerts</span>
            {unread > 0 && (
              <button type="button" className="ml-auto" onClick={() => readAll.mutate()}
                style={{ fontSize: 12, color: 'rgb(21,94,239)' }}>
                Mark all as read
              </button>
            )}
          </div>
          <div style={{ maxHeight: 400, overflowY: 'auto' }}>
            {items.length === 0 && <div style={{ padding: 16, fontSize: 13, color: MUTED }}>No alerts.</div>}
            {items.map((a) => (
              <button key={a.id} type="button" onClick={() => go(a)} className="block w-full text-left hover:bg-[rgb(249,250,251)]"
                style={{ padding: '10px 14px', borderBottom: `1px solid ${LINE}`,
                  borderLeft: `3px solid ${a.urgent ? 'rgb(240,68,56)' : 'transparent'}`,
                  backgroundColor: a.read ? '#fff' : 'rgb(248,250,255)' }}>
                <div style={{ fontSize: 13, fontWeight: a.read ? 400 : 600, color: a.urgent ? 'rgb(180,35,24)' : TEXT }}>
                  {a.title}
                </div>
                {a.body && <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{a.body}</div>}
                <div style={{ fontSize: 11, color: 'rgb(152,162,179)', marginTop: 4 }}>{stamp(a.created_at)}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
