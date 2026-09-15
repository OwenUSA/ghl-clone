import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import {
  addAiKbItem, aiGaps, aiKbItems, aiKbs, createAiKb, deleteAiKb, deleteAiKbItem, dismissAiGap,
  resolveAiGap, searchAiKbs, updateAiKb, updateAiKbItem, uploadAiKbFile,
  type AiGap, type AiKb, type AiKbItem,
} from '../../lib/api'
import type { Me } from '../../lib/auth'
import { fileProblem, formatBytes, isAiAdmin, stamp } from '../../lib/aiAgents'
import { IconChevronLeft } from '../Icon'
import { IconPencil, IconPlus, IconSearchSmall, IconTrashOutline } from '../PipelineIcons'
import {
  BLUE, Chip, Confirm, Empty, Field, INK, INPUT, LINE, Modal, MUTED, Notice, PageHeader, primarySmall,
  RowMenu, smallButton, SubTabs, TableCard, Td, textarea, Th, FAINT, TEXT,
} from './aiUi'

type Kind = 'faq' | 'article' | 'file'

/**
 * Knowledge Base: the list of knowledge bases, one knowledge base's FAQs · Articles · Files,
 * a Test search box, and (ADMIN) Knowledge gaps — questions agents could not answer,
 * resolved by writing the FAQ. A file keeps its extracted text, not its bytes.
 */
export function KnowledgeTab({ user }: { user: Me }) {
  const admin = isAiAdmin(user)
  const [open, setOpen] = useState<number | null>(null)
  const [view, setView] = useState<'bases' | 'gaps'>('bases')
  if (open != null) return <KbDetail user={user} kbId={open} onBack={() => setOpen(null)} />
  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 16px' }}>
      {admin && (
        <div style={{ marginBottom: 12 }}>
          <SubTabs label="Knowledge views" active={view} onSelect={setView}
            tabs={[{ key: 'bases', label: 'Knowledge bases' }, { key: 'gaps', label: 'Knowledge gaps' }]} />
        </div>
      )}
      {view === 'gaps' && admin ? <GapsView /> : <KbList user={user} onOpen={setOpen} />}
    </div>
  )
}

function KbList({ user, onOpen }: { user: Me; onOpen: (id: number) => void }) {
  const qc = useQueryClient()
  const admin = isAiAdmin(user)
  const kbs = useQuery({ queryKey: ['ai-kbs'], queryFn: aiKbs })
  const [dialog, setDialog] = useState<AiKb | 'new' | null>(null)
  const [deleting, setDeleting] = useState<AiKb | null>(null)
  const [menu, setMenu] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const remove = useMutation({
    mutationFn: deleteAiKb,
    onSuccess: () => { setDeleting(null); qc.invalidateQueries({ queryKey: ['ai-kbs'] }) },
    onError: (e: Error) => { setDeleting(null); setError(e.message) },
  })
  return (
    <>
      <PageHeader title="Knowledge Base"
        subtitle="FAQs, articles and files agents search to answer questions about the business. Customer details come from the CRM.">
        {admin && (
          <button type="button" onClick={() => setDialog('new')} style={primarySmall}>
            <IconPlus size={14} color="#fff" /> New knowledge base
          </button>
        )}
      </PageHeader>
      <Notice error={error} onDismiss={() => setError(null)} />
      <TableCard>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 760 }}>
          <thead><tr>
            <Th>Knowledge base</Th><Th width={90} align="right">FAQs</Th><Th width={90} align="right">Articles</Th>
            <Th width={90} align="right">Files</Th><Th width={220}>Used by</Th><Th width={170}>Updated on</Th>
            {admin && <Th width={80} align="center">Actions</Th>}
          </tr></thead>
          <tbody>
            {(kbs.data ?? []).map((kb) => (
              <tr key={kb.id} className="hover:bg-[rgb(249,250,251)]">
                <Td>
                  <button type="button" onClick={() => onOpen(kb.id)} style={{ fontSize: 13, color: 'rgb(0,78,235)', fontWeight: 500 }}>
                    {kb.name}
                  </button>
                  {kb.description && <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{kb.description}</div>}
                </Td>
                <Td align="right">{kb.faqs}</Td><Td align="right">{kb.articles}</Td><Td align="right">{kb.files}</Td>
                <Td><span style={{ fontSize: 12, color: MUTED }}>{kb.agents.length ? kb.agents.join(', ') : '—'}</span></Td>
                <Td>{stamp(kb.updated_at)}</Td>
                {admin && (
                  <Td align="center">
                    <RowMenu label={`Actions for ${kb.name}`} open={menu === kb.id}
                      onToggle={() => setMenu((m) => (m === kb.id ? null : kb.id))} onClose={() => setMenu(null)}
                      items={[
                        { label: 'Rename', icon: <IconPencil size={16} color={INK} />, run: () => setDialog(kb) },
                        { label: 'Delete', icon: <IconTrashOutline size={16} color={INK} />, run: () => setDeleting(kb) },
                      ]} />
                  </Td>
                )}
              </tr>
            ))}
            {kbs.isSuccess && kbs.data.length === 0 && (
              <tr><td colSpan={7}><Empty>No knowledge bases yet.</Empty></td></tr>
            )}
          </tbody>
        </table>
      </TableCard>
      {dialog && (
        <KbModal kb={dialog === 'new' ? null : dialog} onClose={() => setDialog(null)}
          onSaved={(kb) => { setDialog(null); qc.invalidateQueries({ queryKey: ['ai-kbs'] }); if (dialog === 'new') onOpen(kb.id) }} />
      )}
      {deleting && (
        <Confirm title={`Delete “${deleting.name}”?`} danger confirmLabel="Delete" busy={remove.isPending}
          body="Its FAQs, articles and files are deleted. An agent that uses it must be detached and republished first."
          onCancel={() => setDeleting(null)} onConfirm={() => remove.mutate(deleting.id)} />
      )}
    </>
  )
}

function KbModal({ kb, onClose, onSaved }: { kb: AiKb | null; onClose: () => void; onSaved: (kb: AiKb) => void }) {
  const [name, setName] = useState(kb?.name ?? '')
  const [description, setDescription] = useState(kb?.description ?? '')
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => (kb ? updateAiKb(kb.id, { name: name.trim(), description: description.trim() || null })
      : createAiKb({ name: name.trim(), description: description.trim() || null })),
    onSuccess: onSaved, onError: (e: Error) => setError(e.message),
  })
  return (
    <Modal title={kb ? 'Edit knowledge base' : 'New knowledge base'} onClose={onClose}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" disabled={!name.trim()} onClick={() => save.mutate()}
          style={{ ...primarySmall, opacity: name.trim() ? 1 : 0.5 }}>{kb ? 'Save' : 'Create'}</button>
      </>}>
      <Field label="Name" required>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} style={INPUT} aria-label="Knowledge base name" />
      </Field>
      <Field label="Description">
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} style={textarea}
          aria-label="Knowledge base description" />
      </Field>
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => {
      const s = String(r.result)
      resolve(s.slice(s.indexOf(',') + 1))
    }
    r.onerror = () => reject(new Error('the file could not be read'))
    r.readAsDataURL(file)
  })
}

function KbDetail({ user, kbId, onBack }: { user: Me; kbId: number; onBack: () => void }) {
  const qc = useQueryClient()
  const admin = isAiAdmin(user)
  const kbs = useQuery({ queryKey: ['ai-kbs'], queryFn: aiKbs })
  const items = useQuery({ queryKey: ['ai-kb-items', kbId], queryFn: () => aiKbItems(kbId) })
  const kb = (kbs.data ?? []).find((k) => k.id === kbId)
  const [kind, setKind] = useState<Kind>('faq')
  const [editing, setEditing] = useState<AiKbItem | 'new' | null>(null)
  const [deleting, setDeleting] = useState<AiKbItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['ai-kb-items', kbId] })
    qc.invalidateQueries({ queryKey: ['ai-kbs'] })
  }
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const problem = fileProblem(file.name, file.size)
      if (problem) throw new Error(problem)
      const data = await readBase64(file)
      return uploadAiKbFile(kbId, { filename: file.name, content_type: file.type || null, data })
    },
    onSuccess: (item) => { refresh(); setError(null); setNotice(`Added “${item.title}” — ${item.characters.toLocaleString()} characters of text.`) },
    onError: (e: Error) => { setNotice(null); setError(e.message) },
  })
  const remove = useMutation({
    mutationFn: deleteAiKbItem,
    onSuccess: () => { setDeleting(null); refresh() },
    onError: (e: Error) => { setDeleting(null); setError(e.message) },
  })

  const rows = (items.data ?? []).filter((i) => i.kind === kind)
  const count = (k: Kind) => (items.data ?? []).filter((i) => i.kind === k).length

  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 16px' }}>
      <button type="button" onClick={onBack} className="flex items-center gap-4" style={{ fontSize: 13, color: MUTED }}>
        <IconChevronLeft size={16} color={MUTED} /> Knowledge bases
      </button>
      <div style={{ marginTop: 8 }}>
        <PageHeader title={kb?.name ?? 'Knowledge base'} subtitle={kb?.description ?? undefined}>
          {admin && kind !== 'file' && (
            <button type="button" onClick={() => setEditing('new')} style={primarySmall}>
              <IconPlus size={14} color="#fff" /> {kind === 'faq' ? 'Add FAQ' : 'Add article'}
            </button>
          )}
          {admin && kind === 'file' && (
            <>
              <input ref={fileRef} type="file" accept=".pdf,.docx" aria-label="Upload file" style={{ display: 'none' }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = '' }} />
              <button type="button" onClick={() => fileRef.current?.click()} disabled={upload.isPending}
                style={{ ...primarySmall, opacity: upload.isPending ? 0.6 : 1 }}>
                <IconPlus size={14} color="#fff" /> {upload.isPending ? 'Reading file…' : 'Upload file'}
              </button>
            </>
          )}
        </PageHeader>
      </div>
      <Notice error={error} notice={notice} onDismiss={() => { setError(null); setNotice(null) }} />
      <div style={{ marginTop: 12 }}>
        <SubTabs label="Item kinds" active={kind} onSelect={setKind} tabs={[
          { key: 'faq', label: `FAQs (${count('faq')})` },
          { key: 'article', label: `Articles (${count('article')})` },
          { key: 'file', label: `Files (${count('file')})` },
        ]} />
      </div>

      <TableCard>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 760 }}>
          <thead><tr>
            <Th>{kind === 'faq' ? 'Question' : kind === 'article' ? 'Title' : 'File'}</Th>
            {kind === 'file' ? <><Th width={110} align="right">Size</Th><Th width={130} align="right">Characters</Th></>
              : <Th>{kind === 'faq' ? 'Answer' : 'Text'}</Th>}
            <Th width={170}>Updated on</Th>
            {admin && <Th width={110} align="center">Actions</Th>}
          </tr></thead>
          <tbody>
            {rows.map((i) => (
              <tr key={i.id}>
                <Td><span style={{ fontWeight: 500 }}>{i.title}</span></Td>
                {kind === 'file' ? (
                  <><Td align="right">{formatBytes(i.size_bytes)}</Td><Td align="right">{i.characters.toLocaleString()}</Td></>
                ) : (
                  <Td><div style={{ fontSize: 12, color: MUTED, whiteSpace: 'pre-wrap', maxHeight: 60, overflow: 'hidden' }}>{i.body}</div></Td>
                )}
                <Td>{stamp(i.updated_at)}</Td>
                {admin && (
                  <Td align="center">
                    <div className="flex items-center justify-center gap-4">
                      {kind !== 'file' && (
                        <button type="button" aria-label={`Edit ${i.title}`} onClick={() => setEditing(i)} style={{ padding: 4 }}>
                          <IconPencil size={16} color={MUTED} />
                        </button>
                      )}
                      <button type="button" aria-label={`Delete ${i.title}`} onClick={() => setDeleting(i)} style={{ padding: 4 }}>
                        <IconTrashOutline size={16} color={MUTED} />
                      </button>
                    </div>
                  </Td>
                )}
              </tr>
            ))}
            {items.isSuccess && rows.length === 0 && (
              <tr><td colSpan={5}><Empty>
                {kind === 'faq' ? 'No FAQs yet.' : kind === 'article' ? 'No articles yet.'
                  : 'No files yet. PDF and Word (.docx) files up to 10 MB; the text is kept, not the file.'}
              </Empty></td></tr>
            )}
          </tbody>
        </table>
      </TableCard>

      <TestSearch kbId={kbId} />

      {editing && (
        <ItemModal kbId={kbId} kind={kind === 'file' ? 'article' : kind} item={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh() }} />
      )}
      {deleting && (
        <Confirm title={`Delete “${deleting.title}”?`} danger confirmLabel="Delete" busy={remove.isPending}
          body="Agents stop finding it immediately." onCancel={() => setDeleting(null)}
          onConfirm={() => remove.mutate(deleting.id)} />
      )}
    </div>
  )
}

function ItemModal({ kbId, kind, item, onClose, onSaved }: {
  kbId: number; kind: 'faq' | 'article'; item: AiKbItem | null; onClose: () => void; onSaved: () => void
}) {
  const [title, setTitle] = useState(item?.title ?? '')
  const [body, setBody] = useState(item?.body ?? '')
  const [error, setError] = useState<string | null>(null)
  const realKind = item ? (item.kind === 'faq' ? 'faq' : 'article') : kind
  const save = useMutation({
    mutationFn: () => (item ? updateAiKbItem(item.id, { title: title.trim(), body: body.trim() })
      : addAiKbItem(kbId, { kind: realKind, title: title.trim(), body: body.trim() })),
    onSuccess: onSaved, onError: (e: Error) => setError(e.message),
  })
  const faq = realKind === 'faq'
  const ok = title.trim() && body.trim()
  return (
    <Modal title={`${item ? 'Edit' : 'Add'} ${faq ? 'FAQ' : 'article'}`} onClose={onClose} width={640}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" disabled={!ok} onClick={() => save.mutate()} style={{ ...primarySmall, opacity: ok ? 1 : 0.5 }}>
          Save
        </button>
      </>}>
      <Field label={faq ? 'Question' : 'Title'} required>
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} style={INPUT} aria-label={faq ? 'Question' : 'Title'} />
      </Field>
      <Field label={faq ? 'Answer' : 'Text'} required>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} style={{ ...textarea, minHeight: faq ? 120 : 260 }}
          aria-label={faq ? 'Answer' : 'Text'} />
      </Field>
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}

function TestSearch({ kbId }: { kbId: number }) {
  const [q, setQ] = useState('')
  const search = useMutation({ mutationFn: () => searchAiKbs(q.trim(), [kbId]) })
  return (
    <div className="bg-white" style={{ marginTop: 16, border: `1px solid ${LINE}`, borderRadius: 4, padding: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>Test search</div>
      <div style={{ fontSize: 12, color: FAINT, marginTop: 2 }}>What an agent attached to this knowledge base would find.</div>
      <form className="flex gap-8" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); if (q.trim()) search.mutate() }}>
        <label className="flex flex-1 items-center gap-8" style={{ height: 36, padding: '0 10px', border: '1px solid rgb(208,213,221)', borderRadius: 6 }}>
          <IconSearchSmall size={14} color={MUTED} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. do you offer a warranty"
            aria-label="Test search" style={{ width: '100%', fontSize: 14, outline: 'none', color: INK }} />
        </label>
        <button type="submit" disabled={!q.trim()} style={{ ...smallButton, height: 36, opacity: q.trim() ? 1 : 0.5 }}>Search</button>
      </form>
      {search.isError && <div role="alert" style={{ marginTop: 8, fontSize: 13, color: 'rgb(180,35,24)' }}>{(search.error as Error).message}</div>}
      {search.data && (
        <div style={{ marginTop: 10 }} aria-label="Search results">
          {search.data.results.length === 0 && <div style={{ fontSize: 13, color: MUTED }}>Nothing found — an agent would record a knowledge gap.</div>}
          {search.data.results.length > 0 && !search.data.useful && (
            <div style={{ fontSize: 12, color: 'rgb(181,71,8)' }}>Weak matches only — an agent would treat this as not answered.</div>
          )}
          {search.data.results.map((r) => (
            <div key={r.item_id} style={{ padding: '8px 0', borderBottom: `1px solid ${LINE}` }}>
              <div className="flex items-center gap-8">
                <span style={{ fontSize: 13, fontWeight: 500, color: TEXT }}>{r.title}</span>
                <Chip text={r.kind === 'faq' ? 'FAQ' : r.kind === 'article' ? 'Article' : 'File'} fg="rgb(71,84,103)" bg="rgb(242,244,247)" />
                <span className="ml-auto" style={{ fontSize: 11, color: FAINT }}>score {r.score}</span>
              </div>
              <div style={{ fontSize: 12, color: MUTED, marginTop: 4, whiteSpace: 'pre-wrap' }}>{r.text.slice(0, 400)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function GapsView() {
  const qc = useQueryClient()
  const [status, setStatus] = useState<'open' | 'resolved' | 'dismissed'>('open')
  const gaps = useQuery({ queryKey: ['ai-gaps', status], queryFn: () => aiGaps(status) })
  const [resolving, setResolving] = useState<AiGap | null>(null)
  const [error, setError] = useState<string | null>(null)
  const dismiss = useMutation({
    mutationFn: dismissAiGap,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-gaps'] }),
    onError: (e: Error) => setError(e.message),
  })
  return (
    <>
      <PageHeader title="Knowledge gaps"
        subtitle="Questions agents could not answer from their knowledge bases. Answer one to add it as an FAQ." />
      <Notice error={error} onDismiss={() => setError(null)} />
      <TableCard toolbar={
        <div className="flex gap-4">
          {(['open', 'resolved', 'dismissed'] as const).map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}
              style={{ height: 26, padding: '0 10px', borderRadius: 13, fontSize: 12, fontWeight: 500,
                color: status === s ? 'rgb(0,78,235)' : 'rgb(71,84,103)',
                backgroundColor: status === s ? 'rgb(239,244,255)' : 'transparent', border: `1px solid ${LINE}` }}>
              {s[0].toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
      }>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 760 }}>
          <thead><tr>
            <Th>Question</Th><Th width={80} align="right">Asked</Th><Th width={170}>Last seen</Th>
            <Th width={160}>Agent</Th><Th width={80} align="right">Run</Th>
            {status === 'open' && <Th width={170} align="center">Actions</Th>}
          </tr></thead>
          <tbody>
            {(gaps.data ?? []).map((g) => (
              <tr key={g.id}>
                <Td>{g.question}</Td><Td align="right">{g.count}</Td><Td>{stamp(g.last_seen_at)}</Td>
                <Td>{g.agent_name ?? '—'}</Td><Td align="right">{g.last_run_id != null ? `#${g.last_run_id}` : '—'}</Td>
                {status === 'open' && (
                  <Td align="center">
                    <button type="button" onClick={() => setResolving(g)} style={{ fontSize: 13, color: BLUE, fontWeight: 500, marginRight: 12 }}>Answer</button>
                    <button type="button" onClick={() => dismiss.mutate(g.id)} style={{ fontSize: 13, color: MUTED }}>Dismiss</button>
                  </Td>
                )}
              </tr>
            ))}
            {gaps.isSuccess && gaps.data.length === 0 && (
              <tr><td colSpan={6}><Empty>No {status} knowledge gaps.</Empty></td></tr>
            )}
          </tbody>
        </table>
      </TableCard>
      {resolving && (
        <ResolveGapModal gap={resolving} onClose={() => setResolving(null)}
          onDone={() => { setResolving(null); qc.invalidateQueries({ queryKey: ['ai-gaps'] }); qc.invalidateQueries({ queryKey: ['ai-kbs'] }) }} />
      )}
    </>
  )
}

function ResolveGapModal({ gap, onClose, onDone }: { gap: AiGap; onClose: () => void; onDone: () => void }) {
  const kbs = useQuery({ queryKey: ['ai-kbs'], queryFn: aiKbs })
  const [kbId, setKbId] = useState('')
  const [question, setQuestion] = useState(gap.question)
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState<string | null>(null)
  const resolve = useMutation({
    mutationFn: () => resolveAiGap(gap.id, { knowledge_base_id: Number(kbId), answer: answer.trim(), question: question.trim() }),
    onSuccess: onDone, onError: (e: Error) => setError(e.message),
  })
  const ok = kbId && answer.trim() && question.trim()
  return (
    <Modal title="Answer this question" subtitle={`Asked ${gap.count} time${gap.count === 1 ? '' : 's'}. The answer becomes an FAQ.`}
      onClose={onClose} width={600}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" disabled={!ok} onClick={() => resolve.mutate()} style={{ ...primarySmall, opacity: ok ? 1 : 0.5 }}>Add FAQ</button>
      </>}>
      <Field label="Knowledge base" required>
        <select value={kbId} onChange={(e) => setKbId(e.target.value)} style={INPUT} aria-label="Knowledge base">
          <option value="">Choose a knowledge base</option>
          {(kbs.data ?? []).map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
        </select>
      </Field>
      <Field label="Question" required>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} style={INPUT} aria-label="Question" />
      </Field>
      <Field label="Answer" required>
        <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} style={{ ...textarea, minHeight: 120 }} aria-label="Answer" />
      </Field>
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}
