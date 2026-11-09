import { useState } from 'react'
import type { OwenAiProposal, OwenAiProposalOption } from '../../lib/api'
import { PictureThumb } from './PictureZoom'
import { PROPOSAL_STATUS_LABEL, proposalTarget, relativeDay, safeScopeHtml, usd } from '../../lib/owenAi'
import { BLUE, INK, LINE, MONO, MUTED, RED, chipBtn } from './tokens'

/**
 * Owen AI's proposal drafts (2026-11-09): what it prepared, for the owner to Approve or Reject.
 * Nothing is in Zuper until Approve — then the server creates it as a DRAFT (or fills the empty
 * draft Zuper made for the job), reads it back and says which DTR number it got. It is never
 * sent. Pending drafts show the whole Good / Better / Best preview; decided ones fold to a line
 * (created ones link to the proposal in Zuper, failed ones show Zuper's message).
 *
 * Step pictures (2026-11-09, optional): a created proposal offers "Add step pictures" — the server
 * draws one banner per option from the proposal's own scope (OpenAI, a few cents each), the owner
 * looks at them here, then "Put on the proposal" (only option_image changes; still a draft, never
 * sent) or "Discard". Nothing is automatic.
 */
export type PicturesAction = 'generate' | 'attach' | 'discard'

export function ProposalsView({ proposals, loading, error, sentence, onApprove, onReject, busyId, actionError,
  picturesSentence = null, onPictures, picturesBusy = null }: {
  proposals: OwenAiProposal[]; loading: boolean; error: string | null
  /** Why Approve would be refused right now (the server switch or the registry), or null. */
  sentence: string | null
  onApprove: (p: OwenAiProposal) => Promise<boolean>
  onReject: (p: OwenAiProposal, why: string) => Promise<boolean>
  busyId: number | null; actionError: { id: number; message: string } | null
  /** Why step pictures are switched off on this server, or null. */
  picturesSentence?: string | null
  onPictures?: (p: OwenAiProposal, action: PicturesAction) => Promise<boolean>
  picturesBusy?: { id: number; action: PicturesAction } | null
}) {
  const pending = proposals.filter((p) => p.status === 'pending')
  const rest = proposals.filter((p) => p.status !== 'pending')
  return (
    <div style={{ flex: 1, overflowY: 'auto' }}>
      <div style={{ maxWidth: 980, margin: '0 auto', padding: '12px 12px 24px' }}>
        <div style={{ fontSize: 12, color: MUTED, marginBottom: 8 }}>
          Owen AI prepares these. Approve creates the proposal in Zuper as a <strong>draft</strong> — it is never
          sent to the customer.</div>
        {sentence && (
          <div role="status" style={{ fontSize: 13, color: 'rgb(181,71,8)', background: 'rgb(255,250,235)',
            border: '1px solid rgb(254,223,137)', borderRadius: 8, padding: '6px 10px', marginBottom: 8 }}>
            Approve is switched off: {sentence}</div>
        )}
        {loading && <div style={{ color: MUTED, fontSize: 13 }}>Loading proposals…</div>}
        {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>Could not load proposals: {error}</div>}
        {!loading && !error && proposals.length === 0 && (
          <div style={{ color: MUTED, fontSize: 14, textAlign: 'center', marginTop: 40 }}>
            No proposal drafts yet. Ask Owen AI in a job's chat to prepare one.</div>
        )}
        {pending.length > 0 && (
          <section aria-label="Waiting for you" style={{ marginTop: 6 }}>
            <div style={sectionHead}>Waiting for you · {pending.length}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {pending.map((p) => (
                <PendingCard key={p.id} p={p} blocked={!!sentence} busy={busyId === p.id}
                  onApprove={() => onApprove(p)} onReject={(why) => onReject(p, why)}
                  error={actionError?.id === p.id ? actionError.message : null} />
              ))}
            </div>
          </section>
        )}
        {rest.length > 0 && (
          <section aria-label="Decided" style={{ marginTop: 18 }}>
            <div style={sectionHead}>Decided</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {rest.map((p) => (
                <DecidedCard key={p.id} p={p} picturesSentence={picturesSentence} onPictures={onPictures}
                  picturesBusy={picturesBusy?.id === p.id ? picturesBusy.action : null}
                  actionError={actionError?.id === p.id ? actionError.message : null} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

const sectionHead: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: MUTED, padding: '0 2px 6px',
  textTransform: 'uppercase', letterSpacing: 0.4 }

const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10,
  padding: '10px 12px', minWidth: 0 }

function Header({ p }: { p: OwenAiProposal }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: INK }}>Job #{p.job_number ?? '?'}</span>
        {p.customer_name && <span style={{ fontSize: 13, color: INK }}>{p.customer_name}</span>}
        <span style={{ fontSize: 12, color: MUTED }}>{p.template_name ?? p.template_key}</span>
        {p.created_at && <span style={{ fontSize: 12, color: MUTED }} title={p.created_at}>{relativeDay(p.created_at)}</span>}
      </div>
      <div style={{ fontSize: 15, color: INK, marginTop: 4, fontWeight: 500, wordBreak: 'break-word' }}>{p.title}</div>
    </>
  )
}

function PendingCard({ p, blocked, busy, onApprove, onReject, error }: {
  p: OwenAiProposal; blocked: boolean; busy: boolean
  onApprove: () => Promise<boolean>; onReject: (why: string) => Promise<boolean>; error: string | null
}) {
  const [confirming, setConfirming] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [why, setWhy] = useState('')
  const submitReject = () => {
    const w = why.trim()
    if (!w || busy) return
    void onReject(w).then((ok) => { if (ok) { setRejecting(false); setWhy('') } })
  }
  return (
    <article style={{ ...card, borderLeft: `3px solid ${BLUE}` }}>
      <Header p={p} />
      <div style={{ fontSize: 13, color: MUTED, marginTop: 2 }}>{proposalTarget(p)}</div>
      {p.note && <div style={{ fontSize: 13, color: INK, marginTop: 6, whiteSpace: 'pre-wrap' }}>{p.note}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 8, marginTop: 10 }}>
        {p.options.map((o) => <OptionPreview key={o.name} o={o} />)}
      </div>
      {!confirming && !rejecting && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
          <button type="button" onClick={() => setConfirming(true)} disabled={blocked || busy}
            title={blocked ? 'Approve is switched off on this server' : undefined}
            style={{ fontSize: 13, color: '#fff', background: blocked || busy ? 'rgb(152,162,179)' : BLUE,
              borderRadius: 8, padding: '5px 14px' }}>Approve</button>
          <button type="button" onClick={() => setRejecting(true)} disabled={busy} style={chipBtn}>Reject</button>
        </div>
      )}
      {confirming && (
        <div role="group" aria-label="Confirm approve" style={{ marginTop: 10, padding: '8px 10px', borderRadius: 8,
          background: 'rgb(239,244,255)', border: '1px solid rgb(178,204,255)', fontSize: 13, color: INK }}>
          {p.target.startsWith('fill:')
            ? <>Fill <strong>{p.target_number ?? 'the empty draft'}</strong> in Zuper with these options?</>
            : <>Create this proposal in Zuper as a <strong>new draft</strong>?</>} It stays a draft — nothing is
          sent to the customer.
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            <button type="button" disabled={busy}
              onClick={() => { void onApprove().then(() => setConfirming(false)) }}
              style={{ fontSize: 13, color: '#fff', background: busy ? 'rgb(152,162,179)' : BLUE, borderRadius: 8,
                padding: '5px 14px' }}>{busy ? 'Creating in Zuper…' : 'Yes, create the draft'}</button>
            <button type="button" disabled={busy} onClick={() => setConfirming(false)}
              style={{ fontSize: 12, color: MUTED, padding: '0 6px' }}>Cancel</button>
          </div>
        </div>
      )}
      {rejecting && (
        <form onSubmit={(e) => { e.preventDefault(); submitReject() }} style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <input autoFocus value={why} onChange={(e) => setWhy(e.target.value)} maxLength={1000}
            placeholder="Why? e.g. BETTER is too high" aria-label="Reason for rejecting"
            onKeyDown={(e) => { if (e.key === 'Escape') { setRejecting(false); setWhy('') } }}
            style={{ flex: 1, minWidth: 0, fontSize: 13, color: INK, border: `1px solid ${BLUE}`, borderRadius: 8,
              padding: '5px 8px', outline: 'none', background: '#fff' }} />
          <button type="submit" disabled={!why.trim() || busy}
            style={{ fontSize: 12, color: '#fff', background: why.trim() && !busy ? RED : 'rgb(152,162,179)',
              borderRadius: 8, padding: '0 10px' }}>Reject</button>
          <button type="button" onClick={() => { setRejecting(false); setWhy('') }}
            style={{ fontSize: 12, color: MUTED, padding: '0 4px' }}>Cancel</button>
        </form>
      )}
      {error && <div role="alert" style={{ color: RED, fontSize: 12, marginTop: 6 }}>{error}</div>}
    </article>
  )
}

function OptionPreview({ o }: { o: OwenAiProposalOption }) {
  return (
    <div style={{ border: `1px solid ${o.is_recommended ? 'rgb(178,204,255)' : LINE}`, borderRadius: 8,
      padding: '8px 10px', minWidth: 0, background: o.is_recommended ? 'rgb(248,250,255)' : '#fff' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: INK, letterSpacing: 0.3 }}>{o.name}</span>
        {o.is_recommended && <span style={{ fontSize: 11, color: BLUE, fontWeight: 600 }}>Recommended</span>}
        <span style={{ marginLeft: 'auto', fontSize: 16, fontWeight: 600, color: INK }}>{usd(o.price)}</span>
      </div>
      <div style={{ fontSize: 13, color: INK, marginTop: 2, wordBreak: 'break-word' }}>{o.option_description}</div>
      <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
        Deposit {usd(o.deposit)}{o.product_code && <> · <span style={{ fontFamily: MONO }}>{o.product_code}</span></>}</div>
      <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{o.warranty}</div>
      {o.scope_html && (
        <div className="owen-ai-scope" style={{ marginTop: 6, fontSize: 13, lineHeight: '19px', color: 'rgb(52,64,84)',
          wordBreak: 'break-word' }}
          // Sanitized on the server (only bare <b> <i> <ul> <li> <p> <br>) and filtered again here.
          dangerouslySetInnerHTML={{ __html: safeScopeHtml(o.scope_html) }} />
      )}
    </div>
  )
}

function DecidedCard({ p, picturesSentence, onPictures, picturesBusy, actionError }: {
  p: OwenAiProposal; picturesSentence: string | null
  onPictures?: (p: OwenAiProposal, action: PicturesAction) => Promise<boolean>
  picturesBusy: PicturesAction | null; actionError: string | null
}) {
  const [open, setOpen] = useState(false)
  const color = p.status === 'created' ? 'rgb(6,118,71)' : p.status === 'failed' ? RED : MUTED
  return (
    <article style={card}>
      <Header p={p} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginTop: 4, fontSize: 12 }}>
        <span style={{ color, fontWeight: 600 }}>{PROPOSAL_STATUS_LABEL[p.status] ?? p.status}</span>
        {p.status === 'created' && p.estimate_number && (p.estimate_url
          ? <a href={p.estimate_url} target="_blank" rel="noreferrer" style={{ color: BLUE }}>{p.estimate_number} in Zuper</a>
          : <span style={{ color: INK }}>{p.estimate_number}</span>)}
        {p.job_url && <a href={p.job_url} target="_blank" rel="noreferrer" style={{ color: MUTED }}>Job in Zuper</a>}
        <span style={{ color: MUTED }}>{usd(p.total)} across {p.options.length} option{p.options.length === 1 ? '' : 's'}</span>
        {p.decided_by && p.decided_at && (
          <span style={{ color: MUTED }} title={p.decided_at}>by {p.decided_by} {relativeDay(p.decided_at)}</span>)}
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          style={{ fontSize: 12, color: MUTED, textDecoration: 'underline', textUnderlineOffset: 2 }}>
          {open ? 'Hide options' : 'Options'}</button>
      </div>
      {p.error && (
        <div role={p.status === 'failed' ? 'alert' : undefined} style={{ marginTop: 6, fontSize: 12, lineHeight: '18px',
          color: p.status === 'failed' ? RED : 'rgb(52,64,84)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {p.status === 'rejected' ? 'Why: ' : ''}{p.error}</div>
      )}
      {open && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 8, marginTop: 8 }}>
          {p.options.map((o) => <OptionPreview key={o.name} o={o} />)}
        </div>
      )}
      {p.status === 'created' && onPictures && (
        <StepPictures p={p} sentence={picturesSentence} busy={picturesBusy} error={actionError}
          run={(a) => onPictures(p, a)} />
      )}
    </article>
  )
}

const usd2 = (n: number) => `$${n.toFixed(2)}`

/** The optional step pictures of a created proposal: offer -> making -> preview -> on DTR-N. */
function StepPictures({ p, sentence, busy, error, run }: {
  p: OwenAiProposal; sentence: string | null; busy: PicturesAction | null; error: string | null
  run: (a: PicturesAction) => Promise<boolean>
}) {
  const [confirming, setConfirming] = useState(false)
  const st = p.pictures_status
  const making = busy === 'generate' || st === 'generating'
  const attaching = busy === 'attach' || st === 'attaching'
  const where = p.estimate_number ?? 'the proposal'
  const box: React.CSSProperties = { marginTop: 8, paddingTop: 8, borderTop: `1px dashed ${LINE}`, fontSize: 13,
    color: INK }
  const problem = error ?? p.pictures_error
  if (st === 'attached') {
    return (
      <div style={box}>
        <span style={{ color: 'rgb(6,118,71)', fontWeight: 600 }}>Pictures on {where}</span>
        <div style={{ marginTop: 4 }}>
          {p.pictures.map((x) => <PictureThumb key={x.name} src={x.url} label={x.name} width={150} />)}
        </div>
      </div>
    )
  }
  if (making) {
    return (
      <div style={box} role="status">
        <span aria-hidden="true" style={{ display: 'inline-block', width: 12, height: 12,
          border: `2px solid ${LINE}`, borderTopColor: BLUE, borderRadius: '50%', marginRight: 8,
          verticalAlign: '-2px', animation: 'owen-ai-pictures-spin 0.9s linear infinite' }} />
        Making the step pictures… this takes a minute or two.
        <style>{'@keyframes owen-ai-pictures-spin { to { transform: rotate(360deg) } }'}</style>
      </div>
    )
  }
  if (st === 'ready' || st === 'attaching') {
    return (
      <div style={box}>
        <div style={{ fontSize: 12, color: MUTED, marginBottom: 4 }}>
          Step pictures — preview only, not on {where} yet
          {p.pictures_cost_usd != null && <> · OpenAI cost {usd2(p.pictures_cost_usd)}</>}</div>
        <div>{p.pictures.map((x) => <PictureThumb key={x.name} src={x.url} label={x.name} />)}</div>
        {!confirming ? (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            <button type="button" disabled={!!sentence || attaching || busy !== null} onClick={() => setConfirming(true)}
              title={sentence ?? undefined}
              style={{ fontSize: 13, color: '#fff', background: sentence || attaching ? 'rgb(152,162,179)' : BLUE,
                borderRadius: 8, padding: '5px 14px' }}>{attaching ? `Putting them on ${where}…` : 'Put on the proposal'}</button>
            <button type="button" disabled={attaching || busy !== null} onClick={() => { void run('discard') }}
              style={chipBtn}>{busy === 'discard' ? 'Discarding…' : 'Discard'}</button>
          </div>
        ) : (
          <div role="group" aria-label="Confirm pictures" style={{ marginTop: 8, padding: '8px 10px', borderRadius: 8,
            background: 'rgb(239,244,255)', border: '1px solid rgb(178,204,255)' }}>
            Put these {p.pictures.length} pictures on <strong>{where}</strong>? Only the option pictures change — prices
            and wording stay as they are, it stays a draft, and nothing is sent to the customer.
            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
              <button type="button" disabled={attaching}
                onClick={() => { void run('attach').then(() => setConfirming(false)) }}
                style={{ fontSize: 13, color: '#fff', background: BLUE, borderRadius: 8, padding: '5px 14px' }}>
                {attaching ? 'Putting them on…' : 'Yes, put them on'}</button>
              <button type="button" disabled={attaching} onClick={() => setConfirming(false)}
                style={{ fontSize: 12, color: MUTED, padding: '0 6px' }}>Cancel</button>
            </div>
          </div>
        )}
        {problem && <div role="alert" style={{ color: RED, fontSize: 12, marginTop: 6 }}>{problem}</div>}
      </div>
    )
  }
  // None yet, discarded, or the last try failed.
  return (
    <div style={box}>
      <button type="button" disabled={!!sentence || busy !== null} onClick={() => { void run('generate') }}
        title={sentence ?? "Draws one picture per option from this proposal's scope, for you to look at first"}
        style={{ ...chipBtn, color: sentence ? MUTED : BLUE, borderColor: sentence ? LINE : 'rgb(178,204,255)' }}>
        {st === 'failed' ? 'Try the step pictures again' : 'Add step pictures'} (optional, ~{usd2(p.pictures_estimate_usd)})
      </button>
      {sentence && <span style={{ fontSize: 12, color: MUTED, marginLeft: 8 }}>{sentence}</span>}
      {problem && <div role="alert" style={{ color: RED, fontSize: 12, marginTop: 6 }}>{problem}</div>}
    </div>
  )
}
