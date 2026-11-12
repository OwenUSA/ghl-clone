import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError, owenAiPreviewReport, owenAiReportFileUrl, owenAiReportHtml, owenAiRestoreReport, owenAiSaveReport,
  owenAiSavedReport, owenAiSavedReports, owenAiUploadReportPicture, type OwenAiSavedReportDetail,
} from '../../lib/api'
import {
  PICTURE_STATE_LABEL, SOURCE_LABEL, TIER_COLOR, addPoint, clone, hasPrice, linesToList, movePoint, newArea,
  photoNumber, removePoint, type RArea, type RCoverage, type ROption, type RSpec, type Tier,
} from '../../lib/owenAiReport'
import { BLUE, INK, LINE, MUTED, RED, chipBtn } from './tokens'

/**
 * The Reports tab (2026-11-12): the after-inspection reports Owen AI made, and EVERY part of
 * them editable here — the words, the photo pages, each option's steps, scope, picture text and
 * the area it covers (drag the corners on the photo), the comparison, and anything else in
 * "All fields". A save is a new version; old versions can be opened and restored. Owen AI edits
 * the same saved report from the chat, so a save made from an older version is refused with a
 * sentence (409) instead of overwriting it. Nothing here is sent to the customer.
 */
export function ReportsView() {
  const [openId, setOpenId] = useState<number | null>(null)
  const list = useQuery({ queryKey: ['owen-ai-saved-reports'], queryFn: owenAiSavedReports,
    refetchInterval: 30_000 })
  if (openId != null) return <ReportEditor id={openId} onBack={() => setOpenId(null)} />
  const rows = list.data?.reports ?? []
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
      <div style={{ maxWidth: 820, display: 'grid', gap: 10 }}>
        <div style={{ fontSize: 13, color: MUTED }}>
          After-inspection reports Owen AI made. Open one to change anything in it — every save is a new
          version. Nothing is sent to the customer from here.</div>
        {list.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>}
        {list.isError && <div role="alert" style={{ color: RED, fontSize: 13 }}>{(list.error as Error).message}</div>}
        {!list.isLoading && rows.length === 0 && (
          <div style={{ color: MUTED, fontSize: 13, border: `1px dashed ${LINE}`, borderRadius: 10, padding: 16 }}>
            No reports yet. Ask Owen AI in a chat: <i>"make the inspection report for job 292"</i>.</div>)}
        {rows.map((r) => (
          <button key={r.id} type="button" onClick={() => setOpenId(r.id)}
            style={{ textAlign: 'left', background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10,
              padding: '10px 14px', display: 'grid', gap: 2, cursor: 'pointer' }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: INK }}>
              Job #{r.job_number ?? '?'}{r.customer ? ` · ${r.customer}` : ''}</span>
            <span style={{ fontSize: 12, color: MUTED }}>
              Version {r.current_version} · {SOURCE_LABEL[r.latest.source] ?? r.latest.source}
              {r.latest.note ? ` — ${r.latest.note}` : ''} · {when(r.updated_at)}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function when(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('en-US', { month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' })
}

function ReportEditor({ id, onBack }: { id: number; onBack: () => void }) {
  const qc = useQueryClient()
  const key = ['owen-ai-saved-report', id] as const
  // Polled, so a version Owen AI saves from the chat shows up while the report is open.
  const detail = useQuery({ queryKey: key, queryFn: () => owenAiSavedReport(id), refetchInterval: 15_000 })
  const d = detail.data
  const [draft, setDraft] = useState<RSpec | null>(null)
  const [loadedVersion, setLoadedVersion] = useState<number | null>(null)
  const [loadedSpec, setLoadedSpec] = useState<string>('')
  const [note, setNote] = useState('')
  const [regen, setRegen] = useState<Set<string>>(new Set())
  const [html, setHtml] = useState<string>('')
  const [message, setMessage] = useState<{ text: string; bad: boolean } | null>(null)
  const [conflict, setConflict] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  // "Unsaved" = different from the version this editor loaded (not from the newest one, which
  // Owen AI may have saved meanwhile). A newer version is taken in only when nothing is unsaved.
  const dirty = !!draft && JSON.stringify(draft) !== loadedSpec
  const newer = !!d && loadedVersion != null && d.current_version !== loadedVersion
  useEffect(() => {
    if (!d || (dirty && loadedVersion != null)) return
    if (loadedVersion !== d.current_version) {
      setDraft(clone(d.spec) as RSpec)
      setLoadedSpec(JSON.stringify(d.spec))
      setLoadedVersion(d.current_version)
      owenAiReportHtml(d.latest.html_url).then(setHtml, () => setHtml(''))
    }
  }, [d, dirty, loadedVersion])

  const save = useMutation({
    mutationFn: () => owenAiSaveReport(id, { base_version: loadedVersion ?? d!.current_version, spec: draft!,
      note: note.trim() || undefined, regenerate: [...regen] }),
    onSuccess: async (v) => {
      setNote(''); setRegen(new Set()); setConflict(false)
      setMessage({ text: `Saved as version ${v.number}.${v.cost_usd ? ` New pictures cost $${v.cost_usd.toFixed(2)}.` : ''}`,
        bad: false })
      setLoadedVersion(null)
      await qc.invalidateQueries({ queryKey: key })
      qc.invalidateQueries({ queryKey: ['owen-ai-saved-reports'] })
    },
    onError: (e) => {
      setConflict(e instanceof ApiError && e.status === 409)
      setMessage({ text: (e as Error).message, bad: true })
    },
  })
  const restore = useMutation({
    mutationFn: (n: number) => owenAiRestoreReport(id, n, loadedVersion ?? d!.current_version),
    onSuccess: async (v) => {
      setMessage({ text: `Restored as version ${v.number}.`, bad: false })
      setLoadedVersion(null)
      await qc.invalidateQueries({ queryKey: key })
    },
    onError: (e) => setMessage({ text: (e as Error).message, bad: true }),
  })
  const preview = useMutation({
    mutationFn: () => owenAiPreviewReport(id, draft!),
    onSuccess: (r) => { setHtml(r.html); setMessage({ text: 'Preview of your unsaved changes.', bad: false }) },
    onError: (e) => setMessage({ text: (e as Error).message, bad: true }),
  })

  if (detail.isLoading || !d || !draft) {
    return <div style={{ padding: 16, color: MUTED, fontSize: 13 }}>
      {detail.isError ? (detail.error as Error).message : 'Loading…'}</div>
  }
  const set = (fn: (s: RSpec) => void) => setDraft((old) => { const s = clone(old!); fn(s); return s })
  const priceWarning = hasPrice(JSON.stringify(draft.gbb ?? {}))

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', padding: '8px 12px',
        borderBottom: `1px solid ${LINE}`, background: '#fff' }}>
        <button type="button" onClick={onBack} style={chipBtn}>← Reports</button>
        <span style={{ fontSize: 14, fontWeight: 600, color: INK }}>
          Job #{d.job_number}{d.customer ? ` · ${d.customer}` : ''}</span>
        <span style={{ fontSize: 12, color: MUTED }}>Version {loadedVersion ?? d.current_version}
          {dirty ? ' · unsaved changes' : ''}</span>
        {newer && dirty && <span role="status" style={{ fontSize: 12, color: 'rgb(181,71,8)' }}>
          Version {d.current_version} was saved meanwhile ({SOURCE_LABEL[d.latest.source] ?? d.latest.source}).
          Saving now will be refused.</span>}
        <span style={{ flex: 1 }} />
        <a href={d.latest.html_url} target="_blank" rel="noreferrer" style={chipBtn}>Open page</a>
        {d.latest.pdf_url && <a href={d.latest.pdf_url} style={chipBtn}>Download PDF</a>}
        <button type="button" onClick={() => setShowHistory(!showHistory)} aria-expanded={showHistory}
          style={chipBtn}>History ({d.versions.length})</button>
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexWrap: 'wrap', overflow: 'auto' }}>
        <div style={{ flex: '1 1 420px', minWidth: 0, padding: 12, display: 'grid', gap: 10, alignContent: 'start' }}>
          {showHistory && <History d={d} busy={restore.isPending} onRestore={(n) => restore.mutate(n)} />}
          <SaveBar note={note} setNote={setNote} dirty={dirty || regen.size > 0} saving={save.isPending}
            onSave={() => save.mutate()} onPreview={() => preview.mutate()} previewing={preview.isPending}
            message={message} conflict={conflict} priceWarning={priceWarning}
            onReload={() => { setLoadedVersion(null); setConflict(false); setMessage(null)
              qc.invalidateQueries({ queryKey: key }) }}
            regen={[...regen]} cost={d.picture_cost_usd} />
          {d.latest.warnings.length > 0 && (
            <Section title={`Notes from the last save (${d.latest.warnings.length})`}>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: MUTED }}>
                {d.latest.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></Section>)}
          <Section title="Summary" open>
            <Field label="What happened"><TextArea value={draft.what_happened} rows={4}
              onChange={(v) => set((s) => { s.what_happened = v })} /></Field>
            <Field label="Badge text (severity stays as it is)"><TextArea value={draft.badge.text ?? ''} rows={2}
              onChange={(v) => set((s) => { s.badge.text = v })} /></Field>
            <Field label="Weather"><Input value={draft.weather} onChange={(v) => set((s) => { s.weather = v })} /></Field>
            <Field label="How we inspected"><Input value={draft.inspected_how}
              onChange={(v) => set((s) => { s.inspected_how = v })} /></Field>
            <Field label="Extra sentence for 'What to do next'"><Input value={draft.next_steps ?? ''}
              onChange={(v) => set((s) => { s.next_steps = v })} /></Field>
          </Section>
          <Section title={`Findings (${draft.findings.length})`}>
            {draft.findings.map((f, i) => (
              <div key={i} style={{ display: 'grid', gap: 6, borderTop: i ? `1px solid ${LINE}` : 0, paddingTop: i ? 8 : 0 }}>
                <Field label={`Finding ${i + 1} title`}><Input value={f.title}
                  onChange={(v) => set((s) => { s.findings[i].title = v })} /></Field>
                {(['saw', 'means', 'fix'] as const).map((k) => (
                  <Field key={k} label={{ saw: 'What we saw', means: 'What it means', fix: 'How we fix it' }[k]}>
                    <TextArea value={String(f[k] ?? '')} rows={2}
                      onChange={(v) => set((s) => { s.findings[i][k] = v })} /></Field>))}
              </div>))}
          </Section>
          <Galleries d={d} draft={draft} set={set} />
          {draft.gbb && draft.gbb.options.map((o, i) => (
            <OptionEditor key={o.name} d={d} o={o} regen={regen.has(o.name)}
              onRegen={(on) => setRegen((r) => { const n = new Set(r); if (on) n.add(o.name); else n.delete(o.name); return n })}
              onChange={(fn) => set((s) => fn(s.gbb!.options[i]))} reportId={id} />))}
          {draft.gbb && (
            <Section title="Compare the options">
              <Field label="Intro above the options (optional)"><TextArea value={draft.gbb.intro ?? ''} rows={3}
                onChange={(v) => set((s) => { s.gbb!.intro = v })} /></Field>
              {(draft.gbb.compare ?? []).map((r, i) => (
                <div key={i} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr)) auto', gap: 6 }}>
                  <Input value={r.label} placeholder="Row" onChange={(v) => set((s) => { s.gbb!.compare![i].label = v })} />
                  {draft.gbb!.options.map((o) => (
                    <Input key={o.name} value={r[o.name] ?? ''} placeholder={o.name}
                      onChange={(v) => set((s) => { s.gbb!.compare![i][o.name] = v })} />))}
                  <button type="button" onClick={() => set((s) => { s.gbb!.compare!.splice(i, 1) })}
                    style={chipBtn} aria-label={`Remove row ${r.label}`}>Remove</button>
                </div>))}
              <button type="button" style={chipBtn} onClick={() => set((s) => {
                s.gbb!.compare = [...(s.gbb!.compare ?? []), { label: '' }] })}>Add a row</button>
            </Section>)}
          <AllFields draft={draft} onApply={setDraft} />
        </div>
        <div style={{ flex: '1 1 480px', minWidth: 0, minHeight: 520, padding: 12 }}>
          <iframe title="Report preview" srcDoc={html} sandbox="allow-scripts"
            style={{ width: '100%', height: '100%', minHeight: 520, border: `1px solid ${LINE}`, borderRadius: 10,
              background: '#fff' }} />
        </div>
      </div>
    </div>
  )
}

function SaveBar(p: { note: string; setNote: (v: string) => void; dirty: boolean; saving: boolean
  onSave: () => void; onPreview: () => void; previewing: boolean; message: { text: string; bad: boolean } | null
  conflict: boolean; onReload: () => void; priceWarning: boolean; regen: string[]; cost: number }) {
  return (
    <div style={{ display: 'grid', gap: 6, background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10, padding: 10,
      position: 'sticky', top: 0, zIndex: 2 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        <Input value={p.note} placeholder="What you changed (optional)" onChange={p.setNote} />
        <button type="button" onClick={p.onPreview} disabled={p.previewing} style={chipBtn}>
          {p.previewing ? 'Previewing…' : 'Preview changes'}</button>
        <button type="button" onClick={p.onSave} disabled={!p.dirty || p.saving}
          style={{ ...chipBtn, color: '#fff', background: p.dirty ? BLUE : 'rgb(152,162,179)', border: 0 }}>
          {p.saving ? 'Saving…' : 'Save as new version'}</button>
      </div>
      {p.regen.length > 0 && <div style={{ fontSize: 12, color: MUTED }}>
        Saving will make new pictures for {p.regen.join(', ')} (about ${(p.cost * p.regen.length).toFixed(2)}).</div>}
      {p.priceWarning && <div role="alert" style={{ fontSize: 12, color: RED }}>
        Remove the prices: this report shows none until AHS says how much it covers.</div>}
      {p.message && <div role={p.message.bad ? 'alert' : 'status'}
        style={{ fontSize: 12, color: p.message.bad ? RED : 'rgb(6,118,71)' }}>{p.message.text}
        {p.conflict && <button type="button" onClick={p.onReload} style={{ ...chipBtn, marginLeft: 8 }}>
          Load the latest version (your unsaved changes here are dropped)</button>}</div>}
    </div>
  )
}

function History({ d, busy, onRestore }: { d: OwenAiSavedReportDetail; busy: boolean; onRestore: (n: number) => void }) {
  return (
    <Section title="History" open>
      {d.versions.map((v) => (
        <div key={v.number} style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', fontSize: 12,
          borderBottom: `1px solid ${LINE}`, padding: '4px 0' }}>
          <b style={{ color: INK }}>v{v.number}</b>
          <span style={{ color: MUTED, flex: 1, minWidth: 160 }}>{SOURCE_LABEL[v.source] ?? v.source}
            {v.author ? ` · ${v.author}` : ''}{v.note ? ` — ${v.note}` : ''} · {when(v.created_at)}</span>
          <a href={v.html_url} target="_blank" rel="noreferrer" style={chipBtn}>Open</a>
          {v.number !== d.current_version && (
            <button type="button" disabled={busy} onClick={() => onRestore(v.number)} style={chipBtn}>Restore</button>)}
        </div>))}
    </Section>
  )
}

function Galleries({ d, draft, set }: { d: OwenAiSavedReportDetail; draft: RSpec; set: (fn: (s: RSpec) => void) => void }) {
  const [picking, setPicking] = useState<number | null>(null)
  const gs = draft.galleries ?? []
  return (
    <Section title={`Photo pages (${gs.length})`}>
      {gs.map((g, gi) => (
        <div key={gi} style={{ display: 'grid', gap: 6 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <Input value={g.title} onChange={(v) => set((s) => { s.galleries![gi].title = v })} />
            <button type="button" style={chipBtn} onClick={() => set((s) => { s.galleries!.splice(gi, 1) })}>Remove page</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 6 }}>
            {g.photos.map((p, pi) => (
              <div key={pi} style={{ display: 'grid', gap: 4 }}>
                <img src={owenAiReportFileUrl(d.id, p.photo.path)} alt={p.caption || `Photo ${photoNumber(p.photo.path) ?? ''}`}
                  style={{ width: '100%', aspectRatio: '4 / 3', objectFit: 'cover', borderRadius: 6 }} />
                <Input value={p.caption ?? ''} placeholder="Caption"
                  onChange={(v) => set((s) => { s.galleries![gi].photos[pi].caption = v })} />
                <button type="button" style={chipBtn}
                  onClick={() => set((s) => { s.galleries![gi].photos.splice(pi, 1) })}>Remove</button>
              </div>))}
          </div>
          {picking === gi ? (
            <PhotoPicker d={d} onPick={(path) => { set((s) => { s.galleries![gi].photos.push({ photo: { path }, caption: '' }) })
              setPicking(null) }} onClose={() => setPicking(null)} />
          ) : <button type="button" style={chipBtn} onClick={() => setPicking(gi)}>Add a photo</button>}
        </div>))}
      <button type="button" style={chipBtn} onClick={() => set((s) => {
        s.galleries = [...(s.galleries ?? []), { title: 'Photos', photos: [{ photo: { path: d.library[0] } }] }] })}
        disabled={!d.library.length}>Add a photo page</button>
    </Section>
  )
}

function PhotoPicker({ d, onPick, onClose }: { d: OwenAiSavedReportDetail; onPick: (path: string) => void; onClose: () => void }) {
  return (
    <div style={{ border: `1px solid ${LINE}`, borderRadius: 8, padding: 8, display: 'grid', gap: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: MUTED }}>
        <span>The report's photos ({d.library.length}) — pick one</span>
        <button type="button" style={chipBtn} onClick={onClose}>Close</button></div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(90px, 1fr))', gap: 4,
        maxHeight: 260, overflow: 'auto' }}>
        {d.library.map((path) => (
          <button key={path} type="button" onClick={() => onPick(path)} style={{ padding: 0, border: 0, background: 'none' }}
            aria-label={`Photo ${photoNumber(path) ?? path}`}>
            <img src={owenAiReportFileUrl(d.id, path)} alt="" loading="lazy"
              style={{ width: '100%', aspectRatio: '4 / 3', objectFit: 'cover', borderRadius: 4 }} />
          </button>))}
      </div>
    </div>
  )
}

function OptionEditor({ d, o, onChange, regen, onRegen, reportId }: { d: OwenAiSavedReportDetail; o: ROption
  onChange: (fn: (o: ROption) => void) => void; regen: boolean; onRegen: (on: boolean) => void; reportId: number }) {
  const color = TIER_COLOR[o.name as Tier]
  const pic = d.latest.pictures[o.name]
  const [picking, setPicking] = useState<number | 'new' | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const p = o.picture
  return (
    <Section title={`${o.name} — ${o.headline}`} color={color}>
      <Field label="Headline"><Input value={o.headline} onChange={(v) => onChange((x) => { x.headline = v })} /></Field>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <Field label="Time on site"><Input value={o.days ?? ''} onChange={(v) => onChange((x) => { x.days = v })} /></Field>
        <Field label="Warranty (only if the proposal has none)"><Input value={o.warranty ?? ''}
          onChange={(v) => onChange((x) => { x.warranty = v })} /></Field>
      </div>
      <Field label="Scope of work — one step per line">
        <TextArea value={o.scope.join('\n')} rows={Math.min(14, o.scope.length + 2)}
          onChange={(v) => onChange((x) => { x.scope = linesToList(v) })} /></Field>

      <div style={{ fontSize: 13, fontWeight: 600, color: INK, marginTop: 4 }}>The picture</div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {pic?.file ? <img src={owenAiReportFileUrl(reportId, `pictures/${pic.file}`)} alt={`${o.name} picture`}
          style={{ width: 260, maxWidth: '100%', borderRadius: 6, border: `1px solid ${LINE}` }} />
          : <div style={{ width: 260, maxWidth: '100%', aspectRatio: '3 / 2', border: `1px dashed ${LINE}`, borderRadius: 6,
            display: 'grid', placeItems: 'center', fontSize: 12, color: MUTED }}>No picture yet</div>}
        <div style={{ display: 'grid', gap: 6, fontSize: 12, color: MUTED, flex: 1, minWidth: 200 }}>
          <span>{PICTURE_STATE_LABEL[pic?.state ?? 'missing']}</span>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', color: d.pictures_enabled ? INK : MUTED }}>
            <input type="checkbox" checked={regen} disabled={!d.pictures_enabled}
              onChange={(e) => onRegen(e.target.checked)} />
            Make a new AI picture when I save (about ${d.picture_cost_usd.toFixed(2)})</label>
          {!d.pictures_enabled && <span>AI pictures are switched off on this server.</span>}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <button type="button" style={chipBtn} onClick={() => fileRef.current?.click()}>Upload my own picture</button>
            {o.picture_upload && <button type="button" style={chipBtn}
              onClick={() => onChange((x) => { x.picture_upload = null })}>Use the AI picture again</button>}
          </div>
          <input ref={fileRef} type="file" accept="image/jpeg,image/png" hidden onChange={async (e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (!f) return
            setUploadError(null)
            try {
              const r = await owenAiUploadReportPicture(reportId, f)
              onChange((x) => { x.picture_upload = r.name })
            } catch (err) { setUploadError((err as Error).message) }
          }} />
          {o.picture_upload && <span>Your picture is used once you save.</span>}
          {uploadError && <span role="alert" style={{ color: RED }}>{uploadError}</span>}
        </div>
      </div>
      <Field label="Work sequence — one step per line: Title | short text">
        <TextArea value={p.steps.map((s) => `${s.title} | ${s.text}`).join('\n')} rows={p.steps.length + 1}
          onChange={(v) => onChange((x) => { x.picture.steps = linesToList(v).map((l) => {
            const [title, ...rest] = l.split('|'); return { title: title.trim(), text: rest.join('|').trim() } }) })} />
      </Field>
      <Field label="Overview labels — one per line"><TextArea value={p.callouts.join('\n')} rows={3}
        onChange={(v) => onChange((x) => { x.picture.callouts = linesToList(v) })} /></Field>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <Field label="Section detail title"><Input value={p.section_title}
          onChange={(v) => onChange((x) => { x.picture.section_title = v })} /></Field>
        <Field label="Top view label"><Input value={p.top_label}
          onChange={(v) => onChange((x) => { x.picture.top_label = v })} /></Field>
      </div>
      <Field label="Section layers, top to bottom — one per line"><TextArea value={p.layers.join('\n')} rows={4}
        onChange={(v) => onChange((x) => { x.picture.layers = linesToList(v) })} /></Field>

      <div style={{ fontSize: 13, fontWeight: 600, color: INK, marginTop: 4 }}>Area covered</div>
      {(o.coverage ?? []).map((c, ci) => (
        <div key={ci} style={{ display: 'grid', gap: 6 }}>
          <AreaEditor src={owenAiReportFileUrl(reportId, c.photo.path)} color={color} areas={c.areas}
            onChange={(areas) => onChange((x) => { x.coverage![ci].areas = areas })} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Input value={c.caption ?? ''} placeholder="Caption, e.g. Main roof"
              onChange={(v) => onChange((x) => { x.coverage![ci].caption = v })} />
            <button type="button" style={chipBtn} onClick={() => setPicking(ci)}>Change photo</button>
            <button type="button" style={chipBtn} onClick={() => onChange((x) => { x.coverage!.splice(ci, 1) })}>
              Remove this photo</button>
          </div>
          {picking === ci && <PhotoPicker d={d} onClose={() => setPicking(null)} onPick={(path) => {
            onChange((x) => { x.coverage![ci].photo = { path, crop_gps: c.photo.crop_gps } }); setPicking(null) }} />}
        </div>))}
      {(o.coverage ?? []).length < 3 && (picking === 'new'
        ? <PhotoPicker d={d} onClose={() => setPicking(null)} onPick={(path) => {
          onChange((x) => { x.coverage = [...(x.coverage ?? []), { photo: { path }, areas: [newArea()] } as RCoverage] })
          setPicking(null) }} />
        : <button type="button" style={chipBtn} onClick={() => setPicking('new')}>Add a photo with an area</button>)}
    </Section>
  )
}

/** Drag the corners of each area on the real photo. Points are 0-1 fractions of the picture. */
function AreaEditor({ src, color, areas, onChange }: { src: string; color: string; areas: RArea[]
  onChange: (a: RArea[]) => void }) {
  const svg = useRef<SVGSVGElement>(null)
  const [sel, setSel] = useState<{ a: number; p: number }>({ a: 0, p: 0 })
  const [drag, setDrag] = useState<{ a: number; p: number } | null>(null)
  const at = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect()
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]
  }
  const update = (i: number, a: RArea) => onChange(areas.map((x, k) => (k === i ? a : x)))
  const cur = areas[sel.a]
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <div style={{ position: 'relative', width: '100%', borderRadius: 6, overflow: 'hidden', border: `3px solid ${color}` }}>
        <img src={src} alt="Roof photo" draggable={false} style={{ width: '100%', display: 'block', userSelect: 'none' }} />
        <svg ref={svg} viewBox="0 0 1 1" preserveAspectRatio="none"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', touchAction: 'none' }}
          onPointerMove={(e) => { if (drag) { const [x, y] = at(e); update(drag.a, movePoint(areas[drag.a], drag.p, x, y)) } }}
          onPointerUp={() => setDrag(null)} onPointerLeave={() => setDrag(null)}>
          {areas.map((a, ai) => (
            <g key={ai}>
              <polygon points={a.points.map((p) => p.join(',')).join(' ')}
                fill={a.kind === 'covered' ? color : 'transparent'} fillOpacity={0.35}
                stroke={a.kind === 'covered' ? color : 'rgb(235,238,242)'} strokeWidth={3}
                strokeDasharray={a.kind === 'excluded' ? '8 6' : undefined} vectorEffect="non-scaling-stroke"
                onPointerDown={() => setSel({ a: ai, p: sel.a === ai ? sel.p : 0 })} style={{ cursor: 'pointer' }} />
              {a.points.map((p, pi) => (
                <ellipse key={pi} cx={p[0]} cy={p[1]} rx={0.012} ry={0.016}
                  fill={sel.a === ai && sel.p === pi ? '#fff' : color} stroke="#fff" strokeWidth={2}
                  vectorEffect="non-scaling-stroke" style={{ cursor: 'grab' }}
                  onPointerDown={(e) => { e.stopPropagation(); svg.current?.setPointerCapture(e.pointerId)
                    setSel({ a: ai, p: pi }); setDrag({ a: ai, p: pi }) }} />))}
            </g>))}
        </svg>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', fontSize: 12 }}>
        {areas.map((a, ai) => (
          <button key={ai} type="button" onClick={() => setSel({ a: ai, p: 0 })}
            style={{ ...chipBtn, color: sel.a === ai ? INK : MUTED, borderColor: sel.a === ai ? color : LINE }}>
            Area {ai + 1}{a.label ? `: ${a.label}` : ''}</button>))}
        <button type="button" style={chipBtn} onClick={() => { onChange([...areas, newArea()]); setSel({ a: areas.length, p: 0 }) }}
          disabled={areas.length >= 6}>Add an area</button>
      </div>
      {cur && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <Input value={cur.label ?? ''} placeholder="Label on the photo" onChange={(v) => update(sel.a, { ...cur, label: v })} />
          <select value={cur.kind} aria-label="What this area means"
            onChange={(e) => update(sel.a, { ...cur, kind: e.target.value as RArea['kind'] })}
            style={{ fontSize: 12, border: `1px solid ${LINE}`, borderRadius: 8, padding: '4px 6px' }}>
            <option value="covered">Covered by this option</option>
            <option value="excluded">Not included (dashed)</option>
          </select>
          <button type="button" style={chipBtn} onClick={() => update(sel.a, addPoint(cur, sel.p))}>Add a corner</button>
          <button type="button" style={chipBtn} disabled={cur.points.length <= 3}
            onClick={() => { update(sel.a, removePoint(cur, sel.p)); setSel({ a: sel.a, p: 0 }) }}>Remove the white corner</button>
          <button type="button" style={chipBtn} disabled={areas.length <= 1}
            onClick={() => { onChange(areas.filter((_, k) => k !== sel.a)); setSel({ a: 0, p: 0 }) }}>Delete this area</button>
        </div>)}
    </div>
  )
}

function AllFields({ draft, onApply }: { draft: RSpec; onApply: (s: RSpec) => void }) {
  const text = useMemo(() => JSON.stringify(draft, null, 2), [draft])
  const [value, setValue] = useState(text)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setValue(text), [text])
  return (
    <Section title="All fields (advanced)">
      <div style={{ fontSize: 12, color: MUTED }}>Everything in the report, including what the sections above do not
        show. Edit, press Apply, then Preview or Save. The server checks it on save.</div>
      <textarea value={value} onChange={(e) => setValue(e.target.value)} rows={18} spellCheck={false}
        aria-label="All fields as JSON"
        style={{ width: '100%', fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 12,
          border: `1px solid ${LINE}`, borderRadius: 8, padding: 8 }} />
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button type="button" style={chipBtn} onClick={() => {
          try { onApply(JSON.parse(value) as RSpec); setError(null) } catch (e) { setError((e as Error).message) }
        }}>Apply</button>
        {error && <span role="alert" style={{ fontSize: 12, color: RED }}>That is not valid JSON: {error}</span>}
      </div>
    </Section>
  )
}

function Section({ title, children, open = false, color }: { title: string; children: React.ReactNode
  open?: boolean; color?: string }) {
  return (
    <details open={open} style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10, padding: '8px 12px',
      borderLeft: color ? `5px solid ${color}` : undefined }}>
      <summary style={{ cursor: 'pointer', fontSize: 14, fontWeight: 600, color: color ?? INK }}>{title}</summary>
      <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>{children}</div>
    </details>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: 3, fontSize: 12, color: MUTED, flex: '1 1 180px', minWidth: 0 }}>
      {label}{children}</label>
  )
}

function Input({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)}
    style={{ flex: 1, minWidth: 120, fontSize: 13, color: INK, border: `1px solid ${LINE}`, borderRadius: 8,
      padding: '5px 8px', background: '#fff' }} />
}

function TextArea({ value, onChange, rows = 3 }: { value: string; onChange: (v: string) => void; rows?: number }) {
  return <textarea value={value} rows={rows} onChange={(e) => onChange(e.target.value)}
    style={{ width: '100%', fontSize: 13, color: INK, border: `1px solid ${LINE}`, borderRadius: 8, padding: '6px 8px',
      resize: 'vertical', fontFamily: 'inherit' }} />
}
