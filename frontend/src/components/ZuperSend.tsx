import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { sendToZuper } from '../lib/api'
import { sendButton, sendView, stamp, type OpportunityZuper } from '../lib/zuper'
import { Chip, Confirm } from './ZuperMoneyPanel'
import { BUTTON, DANGER, FAINT, PRIMARY, dead } from './opportunity/ui'

/**
 * The opportunity modal's Zuper line, under its title (Zuper v2, 2026-09-16).
 *
 *   not sent   [Send to Zuper]              — ADMIN / DISPATCHER without "Only assigned data"
 *                                              (`may_send`); disabled with the missing items
 *   queued     Sending to Zuper…
 *   sent       [Managed in Zuper]  Open in Zuper ↗   — the link only when Zuper gave one
 *   failed     <why it failed>  [Send to Zuper]
 *
 * A technician or restricted user gets no button at all, never a dead one. Sending asks
 * first: from then on the card's stage, schedule, address, value, title, status, pipeline
 * and owner — and its customer's details — are changed in Zuper, not here.
 */
export function ZuperSendLine({ opportunityId, zuper }: {
  opportunityId: number
  zuper: OpportunityZuper | undefined
}) {
  const qc = useQueryClient()
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const send = useMutation({
    mutationFn: () => sendToZuper(opportunityId),
    onSuccess: (r) => {
      setAsking(false)
      setNote(r.already ? 'This card was already sent to Zuper.' : null)
      qc.invalidateQueries({ queryKey: ['opportunity', opportunityId] })
      qc.invalidateQueries({ queryKey: ['opportunities'] })
      qc.invalidateQueries({ queryKey: ['opportunity-zuper', opportunityId] })
    },
    onError: (e: Error) => { setAsking(false); setError(e.message) },
  })
  const view = sendView(zuper)
  const button = sendButton(zuper)
  if (view === 'none' && !error) return null

  return (
    <div data-zuper-line={zuper?.state} className="flex flex-wrap items-center gap-3"
      style={{ marginTop: 10 }}>
      {view === 'managed' && (
        <>
          <span data-testid="managed-in-zuper"><Chip text="Managed in Zuper" tone="blue" /></span>
          {zuper?.job_url && (
            <a href={zuper.job_url} target="_blank" rel="noopener noreferrer"
              style={{ fontSize: 14, fontWeight: 500, color: PRIMARY }}>
              Open in Zuper
            </a>
          )}
          {zuper?.sent_at && (
            <span style={{ fontSize: 13, color: FAINT }}>Sent {stamp(zuper.sent_at)}</span>
          )}
        </>
      )}
      {view === 'queued' && (
        <span data-testid="zuper-queued"><Chip text="Sending to Zuper…" tone="amber" /></span>
      )}
      {view === 'failed' && (
        <span role="alert" data-testid="zuper-send-error" style={{ fontSize: 13, color: DANGER }}>
          {zuper?.error ?? 'Sending to Zuper failed.'}
        </span>
      )}
      {button.shown && (
        <button type="button" disabled={!button.enabled || send.isPending}
          title={button.reason ?? 'Send this job to Zuper'}
          onClick={() => { setError(null); setNote(null); setAsking(true) }}
          style={{ ...BUTTON, height: 32, padding: '0 12px', fontSize: 13,
            ...dead(button.enabled && !send.isPending) }}>
          {view === 'failed' ? 'Send to Zuper again' : 'Send to Zuper'}
        </button>
      )}
      {button.shown && button.reason && (
        <span data-testid="zuper-send-problems" style={{ fontSize: 13, color: FAINT }}>
          {button.reason}
        </span>
      )}
      {error && <span role="alert" style={{ fontSize: 13, color: DANGER }}>{error}</span>}
      {note && <span role="status" style={{ fontSize: 13, color: FAINT }}>{note}</span>}
      {asking && (
        <Confirm title="Send this job to Zuper?" action="Send to Zuper" busy={send.isPending}
          onCancel={() => setAsking(false)} onConfirm={() => send.mutate()}>
          The customer and the job are created in Zuper. From then on this card is managed in
          Zuper: its stage, visits, address, value, title, status, pipeline and owner, and the
          customer’s name, phone, email and address, are changed in Zuper. Notes, Checklist
          answers and tasks stay editable here.
        </Confirm>
      )}
    </div>
  )
}
