import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getOpportunityZuperAttachments } from '../lib/api'
import { attachmentsView, stamp, type ZuperAttachment } from '../lib/zuper'
import { BODY, BORDER, DIVIDER, FAINT, MUTED, PRIMARY } from './opportunity/ui'

/**
 * Zuper's job attachments on the Photos tab, beside CompanyCam's (Zuper sync, 2026-09-16).
 *
 *   ── Zuper · 3 files
 *   ▢ ▢ ▢            images: thumbnails, each opens the file in a new tab
 *   report.pdf       other files: a row that opens the file in a new tab
 *
 * Drawn only when the job is linked and Zuper listed something (`attachmentsView`); when Zuper
 * cannot be reached, one line says so; for a sync that is off or a card not linked, nothing.
 * Every `url` is a CRM relay path — the browser never sees Zuper's URL or the key.
 */
export function ZuperPhotos({ opportunityId }: { opportunityId: number }) {
  const { data } = useQuery({
    queryKey: ['zuper-attachments', opportunityId],
    queryFn: () => getOpportunityZuperAttachments(opportunityId),
    staleTime: 60_000,
    retry: false,
  })
  const view = attachmentsView(data)
  if (view === 'none' || !data) return null
  if (view === 'message') {
    return (
      <div role="alert" data-testid="zuper-attachments-unavailable"
        style={{ marginTop: 18, fontSize: 14, color: FAINT, textAlign: 'center' }}>
        {data.message}
      </div>
    )
  }
  const images = data.attachments.filter((a) => a.is_image)
  const files = data.attachments.filter((a) => !a.is_image)
  const n = data.attachments.length
  return (
    <section aria-label="Zuper attachments" style={{ marginTop: 20 }}>
      <div style={{ paddingBottom: 8, marginBottom: 12, borderBottom: '1px solid ' + DIVIDER }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: BODY }}>Zuper</div>
        <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
          {n} {n === 1 ? 'file' : 'files'} on the job in Zuper
        </div>
      </div>
      {images.length > 0 && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
          {images.map((a) => <ImageTile key={a.id} a={a} />)}
        </div>
      )}
      {files.map((a) => (
        <a key={a.id} href={a.url} target="_blank" rel="noopener noreferrer" data-attachment={a.id}
          className="flex items-center gap-3"
          style={{ marginTop: 8, padding: '10px 12px', borderRadius: 8, border: '1px solid ' + BORDER }}>
          <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={MUTED} strokeWidth={1.8}
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" />
          </svg>
          <span className="min-w-0 flex-1 truncate" style={{ fontSize: 14, fontWeight: 500, color: PRIMARY }}>
            {a.name ?? 'Attachment'}
          </span>
          {a.created_at && <span style={{ fontSize: 12, color: MUTED }}>{stamp(a.created_at)}</span>}
        </a>
      ))}
    </section>
  )
}

function ImageTile({ a }: { a: ZuperAttachment }) {
  const [broken, setBroken] = useState(false)
  return (
    <a href={a.url} target="_blank" rel="noopener noreferrer" data-attachment={a.id}
      title={a.name ?? undefined} className="relative block overflow-hidden"
      style={{ aspectRatio: '1 / 1', borderRadius: 8, border: '1px solid ' + BORDER,
        backgroundColor: 'rgb(242,244,247)' }}>
      {broken ? (
        <span className="flex h-full w-full items-center justify-center"
          style={{ fontSize: 12, color: FAINT, padding: 8, textAlign: 'center' }}>
          Photo unavailable
        </span>
      ) : (
        <img src={a.url} alt={a.name ?? 'Zuper attachment'} loading="lazy" decoding="async"
          onError={() => setBroken(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
      )}
    </a>
  )
}
