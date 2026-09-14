import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  dismissCompanyCamReview, getCompanyCamStatus, linkCompanyCamReview, listCompanyCamReview,
  type CompanyCamReviewItem,
} from '../lib/api'
import {
  BODY, BORDER, BUTTON, DANGER, DIVIDER, ErrorLine, FAINT, HEADING, MUTED, PRIMARY_BUTTON,
  TEXT, dead,
} from './opportunity/ui'

/**
 * Settings → CompanyCam. ADMIN only (the section is not drawn for anyone else, and
 * both routes answer 403 regardless).
 *
 *   Connection        token set · project creation · hourly check
 *   Hourly check      last run · last success · last error · last counts
 *   Links             by how they were made; creation requests by state
 *   Review            projects that matched a customer by NAME only — link or dismiss
 *
 * OUR design (GoHighLevel has no CompanyCam page), in the Settings page's own style.
 */
const COUNT_LABELS: [string, string][] = [
  ['scanned', 'Projects scanned'], ['linked_workiz_job', 'Linked by Workiz job number'],
  ['linked_one', 'Linked to one card'], ['linked_several', 'Linked to several cards'],
  ['name_only_review', 'Name-only, for review'], ['unmatched', 'Unmatched'],
  ['already_linked', 'Already linked'],
]
const METHOD_LABELS: Record<string, string> = {
  workiz_job: 'By Workiz job number', address: 'By address', manual: 'By hand',
  created: 'Created from the CRM',
}

function when(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York' }) : '—'
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4" style={{ padding: '6px 0', fontSize: 14 }}>
      <div style={{ width: 220, color: MUTED }}>{label}</div>
      <div className="min-w-0 flex-1" style={{ color: TEXT }}>{children}</div>
    </div>
  )
}

export function CompanyCamSettings() {
  const status = useQuery({ queryKey: ['companycam-status'], queryFn: getCompanyCamStatus })
  const review = useQuery({ queryKey: ['companycam-review'], queryFn: listCompanyCamReview })
  const s = status.data
  const beat = s?.heartbeat
  const card = { border: '1px solid ' + DIVIDER, borderRadius: 8, padding: '12px 16px',
    marginTop: 12, backgroundColor: '#fff' }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 32 }}>
      <section style={{ maxWidth: 860 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: TEXT }}>CompanyCam</div>
        <div style={{ fontSize: 14, color: FAINT, marginTop: 4 }}>
          Job photos stay in CompanyCam. This CRM links each project to its cards and shows the
          photos on the card and on the customer.
        </div>
        {status.isError && <ErrorLine error={(status.error as Error).message} />}
        {s && (
          <>
            <div style={card}>
              <div style={HEADING}>Connection</div>
              <Row label="API token">{s.token_set ? 'Set' : 'Not set — CompanyCam is off'}</Row>
              <Row label="Create projects">{s.create_projects ? 'On' : 'Off'}</Row>
              <Row label="Hourly check">{s.sync_enabled ? 'On' : 'Off'}</Row>
            </div>
            <div style={card}>
              <div style={HEADING}>Hourly check</div>
              <Row label="Last run">{when(beat?.last_started_at)}</Row>
              <Row label="Last success">{when(beat?.last_success_at)}</Row>
              <Row label="Last full sweep">{when(beat?.last_full_sweep_at)}</Row>
              <Row label="Last error">
                <span style={{ color: beat?.last_error ? DANGER : TEXT }}>{beat?.last_error ?? 'None'}</span>
              </Row>
              {beat?.last_counts && COUNT_LABELS.filter(([k]) => k in beat.last_counts!).map(([k, label]) => (
                <Row key={k} label={label}>{String(beat.last_counts![k])}</Row>
              ))}
            </div>
            <div style={card}>
              <div style={HEADING}>Links</div>
              {Object.keys(s.links_by_method).length === 0 && <Row label="Linked projects">None yet</Row>}
              {Object.entries(s.links_by_method).map(([m, n]) => (
                <Row key={m} label={METHOD_LABELS[m] ?? m}>{n}</Row>
              ))}
              {Object.entries(s.project_requests).map(([state, n]) => (
                <Row key={state} label={`Project requests: ${state}`}>{n}</Row>
              ))}
            </div>
          </>
        )}
      </section>

      <section style={{ maxWidth: 860, marginTop: 32 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: TEXT }}>
          Review {review.data ? `(${review.data.length})` : ''}
        </div>
        <div style={{ fontSize: 14, color: FAINT, marginTop: 4 }}>
          These projects match a customer by name only, so they were not linked. Tick the cards a
          project belongs to and link it, or dismiss it.
        </div>
        {review.isError && <ErrorLine error={(review.error as Error).message} />}
        {review.data?.length === 0 && (
          <div style={{ ...card, fontSize: 14, color: FAINT }}>Nothing to review.</div>
        )}
        {review.data?.map((item) => <ReviewRow key={item.id} item={item} />)}
      </section>
    </div>
  )
}

function ReviewRow({ item }: { item: CompanyCamReviewItem }) {
  const qc = useQueryClient()
  const [picked, setPicked] = useState<number[]>([])
  const [error, setError] = useState<string | null>(null)
  const done = () => {
    qc.invalidateQueries({ queryKey: ['companycam-review'] })
    qc.invalidateQueries({ queryKey: ['companycam-status'] })
  }
  const link = useMutation({
    mutationFn: () => linkCompanyCamReview(item.id, picked),
    onSuccess: done, onError: (e: Error) => setError(e.message),
  })
  const dismiss = useMutation({
    mutationFn: () => dismissCompanyCamReview(item.id),
    onSuccess: done, onError: (e: Error) => setError(e.message),
  })
  const busy = link.isPending || dismiss.isPending
  return (
    <div style={{ border: '1px solid ' + BORDER, borderRadius: 10, padding: '12px 14px', marginTop: 12 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: BODY }}>{item.project_name ?? 'Untitled project'}</div>
      <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{item.project_address || 'No address'}</div>
      <div style={{ marginTop: 8 }}>
        {item.candidates.map((c) => (
          <label key={c.id} className="flex items-center gap-2" style={{ fontSize: 14, color: TEXT, padding: '3px 0' }}>
            <input type="checkbox" checked={picked.includes(c.id)}
              onChange={(e) => setPicked((p) => e.target.checked ? [...p, c.id] : p.filter((x) => x !== c.id))} />
            <span className="min-w-0 truncate">
              {c.title}
              <span style={{ color: MUTED }}>
                {' · '}{[c.contact_name, c.pipeline_name, c.address].filter(Boolean).join(' · ')}
              </span>
            </span>
          </label>
        ))}
      </div>
      <ErrorLine error={error} />
      <div className="flex justify-end gap-2" style={{ marginTop: 8 }}>
        <button type="button" style={{ ...BUTTON, height: 36, ...dead(!busy) }} disabled={busy}
          onClick={() => { setError(null); dismiss.mutate() }}>
          Dismiss
        </button>
        <button type="button" disabled={busy || picked.length === 0}
          title={picked.length === 0 ? 'Tick at least one card' : undefined}
          style={{ ...PRIMARY_BUTTON, height: 36, ...dead(!busy && picked.length > 0) }}
          onClick={() => { setError(null); link.mutate() }}>
          {picked.length === 0 ? 'Link' : `Link to ${picked.length} ${picked.length === 1 ? 'card' : 'cards'}`}
        </button>
      </div>
    </div>
  )
}
