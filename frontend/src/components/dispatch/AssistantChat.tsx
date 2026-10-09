import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import {
  ApiError, deleteDispatchChat, dispatchChats, dispatchChatThread, rateDispatchChatMessage,
  renameDispatchChat, sendDispatchChatMessage, uploadDispatchChatFile,
  type DispatchChatDownload, type DispatchChatFile, type DispatchChatFileRef,
  type DispatchChatMessage, type DispatchChatScope, type DispatchChatStep, type DispatchChatSummary,
} from '../../lib/api'
import type { Me } from '../../lib/auth'
import {
  blocks, clip, DEFAULT_FILE_QUESTION, fileSummary, greeting, groupChats, isSpreadsheetName,
  MAX_CHAT_FILES, stepArgs, stepLabel, SUGGESTED_QUESTIONS, type Inline,
} from '../../lib/dispatch'

const INK = 'rgb(16,24,40)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(228,231,236)'
const BLUE = 'rgb(21,94,239)'
const RED = 'rgb(180,35,24)'
const SIDEBAR_BG = 'rgb(244,245,247)'
const SERIF = 'ui-serif, Georgia, Cambria, "Times New Roman", Times, serif'
const NARROW = 900

type Thread = { chat: DispatchChatSummary; can_write: boolean; messages: DispatchChatMessage[] }
/** A spreadsheet on the composer: still uploading, or read by the server and ready to send. */
type Attached = { key: number; filename: string; file: DispatchChatFile | null }

/**
 * The Dispatch assistant (2026-10-01): the page's first tab, laid out like a Claude / ChatGPT
 * chat — a sidebar of saved chats, a greeting and one composer when empty, a centred
 * conversation with the composer pinned below once it starts.
 *
 * The SERVER keeps every conversation (`/api/dispatch/chats`): the browser sends only the new
 * message and the chat it belongs to, never the history. An ADMIN can read everyone's chats,
 * read-only. Each answer can be rated, and shows what it looked up to get there.
 *
 * It reads (the same boards this user may see) and can only RECORD a suggestion; it never
 * changes Zuper or contacts anyone.
 */
export function AssistantChat({ user, activeChatId, setActiveChatId, onOpenSuggestions }: {
  user: Me
  activeChatId: number | null
  setActiveChatId: (id: number | null) => void
  onOpenSuggestions: () => void
}) {
  const qc = useQueryClient()
  const admin = user.role === 'ADMIN'
  const [scope, setScope] = useState<DispatchChatScope>('mine')
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<{ content: string; files: DispatchChatFileRef[] } | null>(null)
  const [sendError, setSendError] = useState<{ chatId: number | null; asked: string;
    files: DispatchChatFileRef[]; message: string } | null>(null)
  const [attached, setAttached] = useState<Attached[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const nextKey = useRef(1)
  const narrow = useNarrow()
  const [sidebarOpen, setSidebarOpen] = useState(() => !isNarrowNow())
  const bottom = useRef<HTMLDivElement>(null)
  const activeRef = useRef(activeChatId)
  activeRef.current = activeChatId

  const chats = useQuery({ queryKey: ['dispatch-chats', scope],
    queryFn: () => dispatchChats(scope) })
  const thread = useQuery({ queryKey: ['dispatch-chat', activeChatId],
    queryFn: () => dispatchChatThread(activeChatId as number), enabled: activeChatId != null,
    retry: (n, e) => !(e instanceof ApiError && (e.status === 404 || e.status === 403)) && n < 2 })

  // A remembered chat that is gone (deleted, or not ours any more) starts a new one instead.
  useEffect(() => {
    const e = thread.error
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) setActiveChatId(null)
  }, [thread.error, setActiveChatId])

  const ask = useMutation({
    mutationFn: (v: { chatId: number | null; content: string; files: DispatchChatFileRef[] }) =>
      sendDispatchChatMessage(v.chatId, v.content, v.files.map((f) => f.id)),
    onMutate: (v) => { setPending({ content: v.content, files: v.files }); setSendError(null) },
    onSuccess: (r, v) => {
      qc.setQueryData<Thread>(['dispatch-chat', r.chat.id], (old) => ({
        chat: r.chat, can_write: old?.can_write ?? true,
        messages: [...(old?.messages ?? []), r.user_message, r.assistant_message],
      }))
      qc.invalidateQueries({ queryKey: ['dispatch-chats'] })
      qc.invalidateQueries({ queryKey: ['dispatch-chat', r.chat.id] })
      if (r.assistant_message.suggestions?.length) {
        qc.invalidateQueries({ queryKey: ['dispatch-suggestions'] })
      }
      // Only follow the answer if the person is still looking at the chat they asked in.
      if (activeRef.current === v.chatId) setActiveChatId(r.chat.id)
    },
    onError: (e, v) => setSendError({ chatId: v.chatId, asked: v.content, files: v.files,
      message: (e as Error).message || 'The assistant could not answer.' }),
    onSettled: () => setPending(null),
  })

  const messages = activeChatId == null ? [] : (thread.data?.messages ?? [])
  const canWrite = activeChatId == null || (thread.data?.can_write ?? true)
  const shownError = sendError && sendError.chatId === activeChatId ? sendError : null

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) },
    [messages.length, ask.isPending, activeChatId, shownError])

  const reading = attached.some((a) => a.file == null)
  const ready = attached.flatMap((a) => a.file ? [a.file] : [])

  const send = (text: string) => {
    const t = text.trim()
    if ((!t && ready.length === 0) || reading || ask.isPending || !canWrite) return
    const files = ready.map((f) => ({ id: f.id, filename: f.filename, total_rows: f.total_rows }))
    setDraft('')
    setAttached([])
    setFileError(null)
    ask.mutate({ chatId: activeChatId, content: t || DEFAULT_FILE_QUESTION, files })
  }

  // Each picked file is uploaded at once, so the chip can say how much the server could read.
  const addFiles = (picked: File[]) => {
    if (!picked.length || !canWrite) return
    setFileError(null)
    let room = MAX_CHAT_FILES - attached.length
    const problems: string[] = []
    for (const f of picked) {
      if (room <= 0) {
        problems.push(`Up to ${MAX_CHAT_FILES} files per message — ${f.name} was not added.`)
        continue
      }
      if (!isSpreadsheetName(f.name)) {
        problems.push(`${f.name} is not an Excel or CSV file (.xlsx, .xlsm or .csv).`)
        continue
      }
      room -= 1
      const key = nextKey.current++
      setAttached((old) => [...old, { key, filename: f.name, file: null }])
      uploadDispatchChatFile(f).then(
        (file) => setAttached((old) => old.map((a) => a.key === key ? { ...a, file } : a)),
        (e: unknown) => {
          setAttached((old) => old.filter((a) => a.key !== key))
          setFileError(`${f.name}: ${uploadProblem(e)}`)
        })
    }
    if (problems.length) setFileError(problems.join(' '))
  }
  const pickFiles = () => { if (canWrite) fileInput.current?.click() }

  const open = (id: number | null) => {
    setActiveChatId(id)
    setSendError(null)
    if (narrow) setSidebarOpen(false)
  }

  const composer = (
    <Composer value={draft} onChange={setDraft} onSend={() => send(draft)} busy={ask.isPending}
      placeholder={messages.length ? 'Reply to the assistant…' : 'How can I help today?'}
      attached={attached} reading={reading} fileError={fileError} onAttach={pickFiles}
      onDropFiles={addFiles} onRemove={(key) => {
        setAttached((old) => old.filter((a) => a.key !== key)); setFileError(null)
      }} />
  )

  const empty = activeChatId == null && pending == null && !shownError
  const sidebar = (
    <ChatSidebar user={user} admin={admin} scope={scope} setScope={setScope}
      chats={chats.data?.chats ?? []} loading={chats.isLoading}
      error={chats.isError ? (chats.error as Error).message : null}
      activeId={activeChatId} onOpen={open} busy={ask.isPending}
      onDeleted={(id) => { if (activeRef.current === id) setActiveChatId(null) }}
      narrow={narrow} onClose={() => setSidebarOpen(false)} />
  )

  let body: React.ReactNode
  if (empty) {
    body = (
      <div className="flex flex-1 flex-col items-center justify-center" style={{ padding: '24px 16px',
        overflowY: 'auto' }}>
        <h1 style={{ fontFamily: SERIF, fontSize: 34, fontWeight: 400, color: INK,
          margin: '0 0 22px', textAlign: 'center' }}>{greeting(user.name)}</h1>
        <div style={{ width: '100%', maxWidth: 680 }}>
          {composer}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 14, justifyContent: 'center' }}>
            <button type="button" onClick={pickFiles} className="hover:bg-[rgb(243,244,246)]"
              style={{ border: `1px solid ${LINE}`, borderRadius: 999, padding: '6px 12px',
                fontSize: 13, color: INK, background: '#fff' }}>Compare my Excel with Zuper</button>
            {SUGGESTED_QUESTIONS.map((q) => (
              <button key={q} type="button" onClick={() => send(q)} className="hover:bg-[rgb(243,244,246)]"
                style={{ border: `1px solid ${LINE}`, borderRadius: 999, padding: '6px 12px',
                  fontSize: 13, color: INK, background: '#fff' }}>{q}</button>
            ))}
          </div>
          <div style={{ textAlign: 'center', fontSize: 12, color: MUTED, marginTop: 16 }}>
            Reads Zuper and every call and text. It never changes Zuper or contacts a customer.
          </div>
        </div>
      </div>
    )
  } else {
    const owner = thread.data?.chat.user_name || 'another user'
    body = (
      <div className="flex min-h-0 flex-1 flex-col">
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <div style={{ maxWidth: 760, margin: '0 auto', padding: '24px 16px 8px' }}>
            {!canWrite && (
              <div style={{ fontSize: 12, color: MUTED, border: `1px solid ${LINE}`, background: '#fff',
                borderRadius: 8, padding: '6px 10px', marginBottom: 8 }}>
                Viewing {owner}'s chat — read only.</div>
            )}
            {activeChatId != null && thread.isLoading && (
              <div style={{ color: MUTED, fontSize: 13 }}>Loading the conversation…</div>)}
            {thread.isError && !(thread.error instanceof ApiError && thread.error.status === 404) && (
              <div style={{ color: RED, fontSize: 13 }}>
                Could not load this chat: {(thread.error as Error).message}</div>)}
            {messages.map((m) => m.role === 'user' ? (
              <UserBubble key={m.id} text={m.content} files={m.files ?? []} />
            ) : (
              <AssistantMessage key={m.id} chatId={activeChatId as number} m={m}
                canRate={canWrite} onOpenSuggestions={onOpenSuggestions} />
            ))}
            {pending != null && ask.variables?.chatId === activeChatId && (
              <>
                <UserBubble text={pending.content} files={pending.files} />
                <div style={{ display: 'flex', margin: '18px 0', alignItems: 'center' }}>
                  <span style={{ color: MUTED, fontSize: 14 }}>Looking through the jobs and calls
                    <span className="dispatch-dots" /></span>
                </div>
              </>
            )}
            {shownError && (
              <>
                <UserBubble text={shownError.asked} files={shownError.files} />
                <div style={{ margin: '18px 0', fontSize: 15, lineHeight: '24px', color: RED }}>
                  {shownError.message}</div>
              </>
            )}
            <div ref={bottom} />
          </div>
        </div>
        {canWrite && (
          <div style={{ padding: '8px 16px 18px', background: 'linear-gradient(transparent, rgb(249,250,251) 30%)' }}>
            <div style={{ maxWidth: 760, margin: '0 auto' }}>
              {composer}
              <div style={{ textAlign: 'center', fontSize: 11, color: MUTED, marginTop: 6 }}>
                Written by AI — check it against the job in Zuper.</div>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1" style={{ position: 'relative', overflow: 'hidden' }}>
      <style>{KEYFRAMES}</style>
      <input ref={fileInput} type="file" accept=".xlsx,.xlsm,.csv" multiple hidden
        onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = '' }} />
      {sidebarOpen && narrow && (
        <div onClick={() => setSidebarOpen(false)} style={{ position: 'absolute', inset: 0,
          background: 'rgba(16,24,40,0.2)', zIndex: 19 }} />
      )}
      {sidebarOpen && sidebar}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px 0' }}>
          <button type="button" onClick={() => setSidebarOpen(!sidebarOpen)}
            aria-expanded={sidebarOpen} style={chipBtn}>
            {sidebarOpen ? 'Hide chats' : 'Chats'}</button>
          {!sidebarOpen && (
            <button type="button" onClick={() => open(null)} style={chipBtn}>+ New chat</button>)}
          {thread.data && activeChatId != null && (
            <span style={{ fontSize: 13, color: MUTED, overflow: 'hidden', textOverflow: 'ellipsis',
              whiteSpace: 'nowrap', minWidth: 0 }}>{thread.data.chat.title}</span>)}
        </div>
        {body}
      </div>
    </div>
  )
}

const chipBtn: React.CSSProperties = { fontSize: 12, color: MUTED, border: `1px solid ${LINE}`,
  borderRadius: 999, padding: '4px 10px', background: '#fff', flexShrink: 0 }

/** The server's sentence for a refused upload, or a plain one by status. */
function uploadProblem(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 413) return e.message || 'The file is larger than 10 MB.'
    if (e.status === 415) return e.message || 'Only .xlsx, .xlsm and .csv files can be read.'
    if (e.status === 422) return e.message || 'The file could not be read.'
  }
  return (e as Error)?.message || 'The file could not be uploaded.'
}

function isNarrowNow(): boolean {
  return typeof window !== 'undefined' && window.innerWidth < NARROW
}

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(isNarrowNow)
  useEffect(() => {
    const on = () => setNarrow(isNarrowNow())
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return narrow
}

// ---------------- the sidebar ----------------

function ChatSidebar({ user, admin, scope, setScope, chats, loading, error, activeId, onOpen,
  busy, onDeleted, narrow, onClose }: {
  user: Me; admin: boolean; scope: DispatchChatScope; setScope: (s: DispatchChatScope) => void
  chats: DispatchChatSummary[]; loading: boolean; error: string | null
  activeId: number | null; onOpen: (id: number | null) => void; busy: boolean
  onDeleted: (id: number) => void; narrow: boolean; onClose: () => void
}) {
  const [menuFor, setMenuFor] = useState<number | null>(null)
  const [renaming, setRenaming] = useState<{ id: number; title: string } | null>(null)
  const [confirming, setConfirming] = useState<number | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const qc = useQueryClient()

  const rename = useMutation({
    mutationFn: (v: { id: number; title: string }) => renameDispatchChat(v.id, v.title),
    onSuccess: (c) => {
      setRenaming(null); setProblem(null)
      qc.invalidateQueries({ queryKey: ['dispatch-chats'] })
      qc.setQueryData<Thread>(['dispatch-chat', c.id], (old) => old ? { ...old, chat: c } : old)
    },
    onError: (e) => setProblem((e as Error).message),
  })
  const remove = useMutation({
    mutationFn: (id: number) => deleteDispatchChat(id),
    onSuccess: (_r, id) => {
      setConfirming(null); setProblem(null)
      onDeleted(id)
      qc.removeQueries({ queryKey: ['dispatch-chat', id] })
      qc.invalidateQueries({ queryKey: ['dispatch-chats'] })
    },
    onError: (e) => setProblem((e as Error).message),
  })

  const saveRename = () => {
    if (!renaming) return
    const t = renaming.title.trim()
    if (!t) { setRenaming(null); return }
    rename.mutate({ id: renaming.id, title: t })
  }

  const groups = groupChats(chats)
  return (
    <aside style={{ width: 260, flexShrink: 0, background: SIDEBAR_BG, borderRight: `1px solid ${LINE}`,
      display: 'flex', flexDirection: 'column', minHeight: 0,
      ...(narrow ? { position: 'absolute', top: 0, bottom: 0, left: 0, zIndex: 20,
        boxShadow: '4px 0 20px rgba(16,24,40,0.12)' } : {}) }}>
      <div style={{ padding: 12, display: 'flex', gap: 6 }}>
        <button type="button" onClick={() => onOpen(null)} disabled={busy}
          className="hover:bg-[rgb(249,250,251)]"
          style={{ flex: 1, textAlign: 'left', fontSize: 14, fontWeight: 500, color: INK,
            border: `1px solid ${LINE}`, borderRadius: 10, padding: '8px 12px', background: '#fff' }}>
          + New chat</button>
        {narrow && <button type="button" onClick={onClose} aria-label="Close chats"
          style={{ ...chipBtn, borderRadius: 10 }}>Close</button>}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        {loading && <div style={{ color: MUTED, fontSize: 12, padding: '4px 8px' }}>Loading…</div>}
        {error && <div style={{ color: RED, fontSize: 12, padding: '4px 8px' }}>
          Could not load chats: {error}</div>}
        {!loading && !error && chats.length === 0 && (
          <div style={{ color: MUTED, fontSize: 12, padding: '4px 8px' }}>
            {scope === 'all' ? 'Nobody has chatted yet.' : 'Your chats will appear here.'}</div>
        )}
        {problem && <div style={{ color: RED, fontSize: 12, padding: '4px 8px' }}>{problem}</div>}
        {groups.map((g) => (
          <div key={g.group} style={{ marginTop: 10 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: MUTED, padding: '0 8px 4px',
              textTransform: 'uppercase', letterSpacing: 0.4 }}>{g.group}</div>
            {g.chats.map((c) => {
              const mine = c.user_id === user.id
              const active = c.id === activeId
              if (renaming?.id === c.id) {
                return (
                  <div key={c.id} style={{ padding: '2px 0' }}>
                    <input autoFocus value={renaming.title} maxLength={200}
                      onChange={(e) => setRenaming({ id: c.id, title: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') { e.preventDefault(); saveRename() }
                        if (e.key === 'Escape') setRenaming(null)
                      }}
                      onBlur={saveRename} disabled={rename.isPending}
                      style={{ width: '100%', fontSize: 13, color: INK, border: `1px solid ${BLUE}`,
                        borderRadius: 8, padding: '6px 8px', outline: 'none', background: '#fff' }} />
                  </div>
                )
              }
              if (confirming === c.id) {
                return (
                  <div key={c.id} style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 8,
                    padding: '8px 10px', margin: '2px 0', fontSize: 12, color: INK }}>
                    <div style={{ marginBottom: 6 }}>Delete “{clip(c.title, 40)}”?</div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button type="button" disabled={remove.isPending} onClick={() => remove.mutate(c.id)}
                        style={{ fontSize: 12, color: '#fff', background: RED, borderRadius: 6,
                          padding: '3px 10px' }}>{remove.isPending ? 'Deleting…' : 'Delete'}</button>
                      <button type="button" onClick={() => setConfirming(null)}
                        style={{ fontSize: 12, color: MUTED, border: `1px solid ${LINE}`, borderRadius: 6,
                          padding: '3px 10px', background: '#fff' }}>Cancel</button>
                    </div>
                  </div>
                )
              }
              return (
                <div key={c.id} className="group" style={{ position: 'relative', margin: '1px 0' }}>
                  <button type="button" onClick={() => { setMenuFor(null); onOpen(c.id) }}
                    title={c.title}
                    className={active ? '' : 'hover:bg-[rgb(234,236,240)]'}
                    style={{ width: '100%', textAlign: 'left', borderRadius: 8,
                      padding: mine ? '7px 30px 7px 10px' : '7px 10px',
                      background: active ? 'rgb(228,231,236)' : 'transparent', display: 'block' }}>
                    <div style={{ fontSize: 13, color: INK, fontWeight: active ? 500 : 400,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {c.title || 'New chat'}</div>
                    {scope === 'all' && (
                      <div style={{ fontSize: 11, color: MUTED, overflow: 'hidden',
                        textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {mine ? 'You' : (c.user_name || 'Unknown user')}</div>
                    )}
                  </button>
                  {mine && (
                    <button type="button" aria-label={`Options for ${c.title}`}
                      onClick={(e) => { e.stopPropagation(); setMenuFor(menuFor === c.id ? null : c.id) }}
                      className={menuFor === c.id || active ? '' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'}
                      style={{ position: 'absolute', right: 4, top: 5, width: 24, height: 24,
                        borderRadius: 6, color: MUTED, fontSize: 16, lineHeight: '20px',
                        background: menuFor === c.id ? '#fff' : 'transparent' }}>⋯</button>
                  )}
                  {menuFor === c.id && (
                    <div style={{ position: 'absolute', right: 4, top: 30, zIndex: 5, background: '#fff',
                      border: `1px solid ${LINE}`, borderRadius: 8, boxShadow: '0 6px 20px rgba(16,24,40,0.12)',
                      padding: 4, minWidth: 120 }}>
                      <button type="button" className="hover:bg-[rgb(243,244,246)]"
                        onClick={() => { setMenuFor(null); setRenaming({ id: c.id, title: c.title }) }}
                        style={menuItem}>Rename</button>
                      <button type="button" className="hover:bg-[rgb(254,243,242)]"
                        onClick={() => { setMenuFor(null); setConfirming(c.id) }}
                        style={{ ...menuItem, color: RED }}>Delete</button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      {admin && (
        <div style={{ borderTop: `1px solid ${LINE}`, padding: 10 }}>
          <div role="group" aria-label="Whose chats" style={{ display: 'flex', background: '#fff',
            border: `1px solid ${LINE}`, borderRadius: 8, padding: 2 }}>
            {(['mine', 'all'] as const).map((s) => (
              <button key={s} type="button" aria-pressed={scope === s} onClick={() => setScope(s)}
                style={{ flex: 1, fontSize: 12, borderRadius: 6, padding: '5px 6px',
                  color: scope === s ? INK : MUTED, fontWeight: scope === s ? 500 : 400,
                  background: scope === s ? 'rgb(234,236,240)' : 'transparent' }}>
                {s === 'mine' ? 'My chats' : "Everyone's chats"}</button>
            ))}
          </div>
        </div>
      )}
    </aside>
  )
}

const menuItem: React.CSSProperties = { display: 'block', width: '100%', textAlign: 'left',
  fontSize: 13, color: INK, padding: '6px 10px', borderRadius: 6 }

// ---------------- messages ----------------

function UserBubble({ text, files = [] }: { text: string; files?: DispatchChatFileRef[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', margin: '14px 0' }}>
      <div style={{ maxWidth: '80%', background: 'rgb(240,238,232)', color: INK,
        borderRadius: 18, padding: '10px 16px', fontSize: 15, lineHeight: '22px',
        whiteSpace: 'pre-wrap' }}>{text}</div>
      {files.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6, justifyContent: 'flex-end',
          maxWidth: '80%' }}>
          {files.map((f) => (
            <span key={f.id} title={f.filename} style={{ ...fileChip, maxWidth: 320 }}>
              <span style={chipName}>{f.filename}</span>
              <span style={{ color: MUTED, flexShrink: 0 }}>· {fileSummary(f.total_rows)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

const fileChip: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 4,
  fontSize: 12, color: INK, border: `1px solid ${LINE}`, borderRadius: 8, padding: '3px 8px',
  background: '#fff', minWidth: 0 }
const chipName: React.CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis',
  whiteSpace: 'nowrap', minWidth: 0 }

function Downloads({ items }: { items: DispatchChatDownload[] }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
      {items.map((d, i) => {
        // A file is downloaded; a page link (the plan's calendar) is opened (2026-10-08).
        const file = /\.(xlsx|csv)(\?|$)/.test(d.url)
        return (
          <a key={`${d.url}-${i}`} href={d.url} download={file || undefined}
            className="hover:bg-[rgb(243,244,246)]"
            style={{ fontSize: 13, color: BLUE, border: `1px solid ${LINE}`, borderRadius: 8,
              padding: '6px 10px', background: '#fff', textDecoration: 'none' }}>
            {file ? 'Download' : 'Open'}: {d.label}</a>
        )
      })}
    </div>
  )
}

function AssistantMessage({ chatId, m, canRate, onOpenSuggestions }: {
  chatId: number; m: DispatchChatMessage; canRate: boolean; onOpenSuggestions: () => void
}) {
  const [showSteps, setShowSteps] = useState(false)
  const steps = m.steps ?? []
  return (
    <div style={{ display: 'flex', margin: '18px 0' }}>
      <div style={{ flex: 1, minWidth: 0, fontSize: 15, lineHeight: '24px',
        color: m.error ? RED : INK }}>
        <Answer text={m.content} />
        {!!m.downloads?.length && <Downloads items={m.downloads} />}
        {!!m.suggestions?.length && (
          <button type="button" onClick={onOpenSuggestions} style={{ marginTop: 8, fontSize: 13,
            color: BLUE, border: `1px solid ${LINE}`, borderRadius: 8, padding: '6px 10px',
            background: '#fff' }}>
            {m.suggestions.length} suggested change{m.suggestions.length === 1 ? '' : 's'} to
            review in Suggestions →</button>
        )}
        {!m.error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
            <Feedback chatId={chatId} m={m} canRate={canRate} />
            {steps.length > 0 && (
              <button type="button" onClick={() => setShowSteps(!showSteps)} aria-expanded={showSteps}
                style={{ fontSize: 12, color: MUTED, textDecoration: 'underline',
                  textUnderlineOffset: 2 }}>
                {showSteps ? 'Hide what it looked up' : `Show what it looked up (${steps.length})`}
              </button>
            )}
          </div>
        )}
        {showSteps && <Steps steps={steps} />}
      </div>
    </div>
  )
}

function Steps({ steps }: { steps: DispatchChatStep[] }) {
  return (
    <ol style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex',
      flexDirection: 'column', gap: 8 }}>
      {steps.map((s, i) => {
        const args = stepArgs(s.args)
        return (
          <li key={i} style={{ fontSize: 12, lineHeight: '18px', color: INK }}>
            <div><span style={{ color: MUTED }}>{i + 1}.</span>{' '}
              <span style={{ fontWeight: 500 }}>{stepLabel(s.tool)}</span>
              {args && <span style={{ color: MUTED }}> — {args}</span>}</div>
            {s.result && (
              <pre style={{ margin: '4px 0 0', padding: '6px 8px', background: 'rgb(243,244,246)',
                border: `1px solid ${LINE}`, borderRadius: 6, color: 'rgb(71,84,103)', fontSize: 11,
                lineHeight: '16px', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}>
                {clip(s.result, 300)}</pre>
            )}
          </li>
        )
      })}
    </ol>
  )
}

function Feedback({ chatId, m, canRate }: { chatId: number; m: DispatchChatMessage; canRate: boolean }) {
  const qc = useQueryClient()
  const rating = m.feedback?.rating ?? null
  const [noteOpen, setNoteOpen] = useState(false)
  const [note, setNote] = useState(m.feedback?.note ?? '')
  const [saved, setSaved] = useState(false)
  const rate = useMutation({
    mutationFn: (v: { rating: 1 | -1; note: string | null }) =>
      rateDispatchChatMessage(chatId, m.id, v.rating, v.note),
    onSuccess: (msg, v) => {
      qc.setQueryData<Thread>(['dispatch-chat', chatId], (old) => old ? { ...old,
        messages: old.messages.map((x) => x.id === msg.id ? msg : x) } : old)
      setSaved(true)
      if (v.rating === 1) setNoteOpen(false)
    },
  })

  const choose = (r: 1 | -1) => {
    if (!canRate || rate.isPending) return
    setSaved(false)
    if (r === -1) { setNoteOpen(true); rate.mutate({ rating: -1, note: m.feedback?.note ?? null }) }
    else rate.mutate({ rating: 1, note: null })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <Thumb up filled={rating === 1} disabled={!canRate || rate.isPending}
          onClick={() => choose(1)} />
        <Thumb up={false} filled={rating === -1} disabled={!canRate || rate.isPending}
          onClick={() => choose(-1)} />
        {saved && !noteOpen && <span style={{ fontSize: 12, color: MUTED, marginLeft: 4 }}>
          Thanks — saved.</span>}
        {rate.isError && <span style={{ fontSize: 12, color: RED, marginLeft: 4 }}>
          {(rate.error as Error).message}</span>}
        {rating === -1 && !noteOpen && m.feedback?.note && (
          <span style={{ fontSize: 12, color: MUTED, marginLeft: 4, maxWidth: 360, overflow: 'hidden',
            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>Note: {m.feedback.note}</span>)}
        {canRate && rating === -1 && !noteOpen && (
          <button type="button" onClick={() => setNoteOpen(true)} style={{ fontSize: 12, color: BLUE,
            marginLeft: 4 }}>{m.feedback?.note ? 'Edit note' : 'Add a note'}</button>)}
      </div>
      {noteOpen && canRate && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start', maxWidth: 520 }}>
          <textarea value={note} rows={2} placeholder="What was wrong or missing?" autoFocus
            onChange={(e) => setNote(e.target.value)}
            style={{ flex: 1, fontSize: 13, lineHeight: '18px', color: INK, border: `1px solid ${LINE}`,
              borderRadius: 8, padding: '6px 8px', resize: 'vertical', background: '#fff', outline: 'none' }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <button type="button" disabled={rate.isPending}
              onClick={() => rate.mutate({ rating: -1, note: note.trim() || null },
                { onSuccess: () => setNoteOpen(false) })}
              style={{ fontSize: 12, color: '#fff', background: BLUE, borderRadius: 6,
                padding: '4px 12px' }}>{rate.isPending ? 'Saving…' : 'Save'}</button>
            <button type="button" onClick={() => setNoteOpen(false)}
              style={{ fontSize: 12, color: MUTED }}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}

function Thumb({ up, filled, disabled, onClick }: {
  up: boolean; filled: boolean; disabled: boolean; onClick: () => void
}) {
  const label = up ? 'Good answer' : 'Bad answer'
  return (
    <button type="button" aria-label={label} title={label} aria-pressed={filled} disabled={disabled}
      onClick={onClick} className={disabled ? '' : 'hover:bg-[rgb(243,244,246)]'}
      style={{ width: 26, height: 26, borderRadius: 6, display: 'flex', alignItems: 'center',
        justifyContent: 'center', color: filled ? INK : MUTED,
        cursor: disabled ? 'default' : 'pointer' }}>
      <svg width={15} height={15} viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"
        aria-hidden="true" style={up ? undefined : { transform: 'rotate(180deg)' }}>
        <path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3z" />
        <path d="M7 10l4-8a3 3 0 0 1 3 3v4h5.5a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.1 21H7" />
      </svg>
    </button>
  )
}

function Composer({ value, onChange, onSend, busy, placeholder, attached, reading, fileError,
  onAttach, onDropFiles, onRemove }: {
  value: string; onChange: (v: string) => void; onSend: () => void; busy: boolean
  placeholder: string; attached: Attached[]; reading: boolean; fileError: string | null
  onAttach: () => void; onDropFiles: (files: File[]) => void; onRemove: (key: number) => void
}) {
  const [dragging, setDragging] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [value])
  const hasFile = attached.some((a) => a.file != null)
  const ready = (value.trim().length > 0 || hasFile) && !reading && !busy
  const full = attached.length >= MAX_CHAT_FILES
  return (
    <div>
    <div onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return
        e.preventDefault(); setDragging(true)
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false)
      }}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return
        e.preventDefault(); setDragging(false)
        onDropFiles(Array.from(e.dataTransfer.files))
      }}
      style={{ background: '#fff', border: `1px ${dragging ? 'dashed' : 'solid'} ${dragging ? BLUE : LINE}`,
        borderRadius: 20, boxShadow: '0 4px 20px rgba(16,24,40,0.06)', padding: '14px 14px 10px 18px' }}>
      {attached.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {attached.map((a) => (
            <span key={a.key} title={a.filename} style={{ ...fileChip, maxWidth: '100%' }}>
              {a.file == null ? (
                <span style={{ ...chipName, color: MUTED }}>Reading {a.filename}…</span>
              ) : (
                <>
                  <span style={chipName}>{a.file.filename}</span>
                  <span style={{ color: MUTED, flexShrink: 0 }}>
                    · {fileSummary(a.file.total_rows, a.file.sheets.length)}</span>
                  <button type="button" onClick={() => onRemove(a.key)}
                    aria-label={`Remove ${a.file.filename}`}
                    style={{ fontSize: 12, color: BLUE, marginLeft: 4, flexShrink: 0 }}>Remove</button>
                </>
              )}
            </span>
          ))}
        </div>
      )}
      {dragging && (
        <div style={{ fontSize: 12, color: BLUE, marginBottom: 6 }}>
          Drop an Excel or CSV file to attach it</div>)}
      <textarea ref={ref} value={value} rows={1} autoFocus placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() } }}
        style={{ width: '100%', resize: 'none', border: 'none', outline: 'none', fontSize: 15,
          lineHeight: '22px', color: INK, background: 'transparent', maxHeight: 200 }} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <button type="button" onClick={onAttach} disabled={full || busy}
            title={full ? `Up to ${MAX_CHAT_FILES} files per message` : 'Attach an .xlsx, .xlsm or .csv file'}
            className={full || busy ? '' : 'hover:bg-[rgb(243,244,246)]'}
            style={{ fontSize: 12, color: full || busy ? 'rgb(152,162,179)' : INK,
              border: `1px solid ${LINE}`, borderRadius: 8, padding: '3px 10px', background: '#fff',
              flexShrink: 0 }}>Attach Excel</button>
          <span style={{ fontSize: 12, color: MUTED, overflow: 'hidden', textOverflow: 'ellipsis',
            whiteSpace: 'nowrap' }}>Dispatch assistant · reads only</span>
        </span>
        <button type="button" aria-label="Send" disabled={!ready} onClick={onSend}
          style={{ width: 32, height: 32, borderRadius: 10, display: 'flex', alignItems: 'center',
            justifyContent: 'center', background: ready ? BLUE : 'rgb(234,236,240)',
            color: ready ? '#fff' : 'rgb(152,162,179)', transition: 'background 120ms' }}>
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" /></svg>
        </button>
      </div>
    </div>
    {fileError && (
      <div role="alert" style={{ color: RED, fontSize: 12, marginTop: 6, padding: '0 6px' }}>
        {fileError}</div>)}
    </div>
  )
}

function Parts({ parts }: { parts: Inline[] }) {
  return <>{parts.map((p, i) => p.bold ? <strong key={i}>{p.text}</strong> : <span key={i}>{p.text}</span>)}</>
}

function Answer({ text }: { text: string }) {
  return (
    <>
      {blocks(text).map((b, i) => b.kind === 'p' ? (
        <p key={i} style={{ margin: '0 0 10px' }}><Parts parts={b.parts} /></p>
      ) : b.kind === 'h' ? (
        <div key={i} style={{ fontWeight: 600, margin: '12px 0 6px' }}><Parts parts={b.parts} /></div>
      ) : b.kind === 'table' ? (
        <div key={i} style={{ overflowX: 'auto', margin: '0 0 12px' }} role="region"
          aria-label="Table">
          <table style={{ borderCollapse: 'collapse', fontSize: 13, lineHeight: '18px',
            minWidth: '100%' }}>
            <thead>
              <tr>{b.head.map((c, k) => (
                <th key={k} style={{ textAlign: 'left', fontWeight: 600, padding: '6px 8px',
                  borderBottom: `2px solid ${LINE}`, background: 'rgb(249,250,251)',
                  verticalAlign: 'bottom' }}><Parts parts={c} /></th>
              ))}</tr>
            </thead>
            <tbody>
              {b.rows.map((r, j) => (
                <tr key={j}>{b.head.map((_, k) => (
                  <td key={k} style={{ padding: '6px 8px', borderBottom: `1px solid ${LINE}`,
                    verticalAlign: 'top', minWidth: k === 0 ? 120 : 140 }}>
                    <Parts parts={r[k] ?? []} /></td>
                ))}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div key={i} style={{ margin: '0 0 10px' }}>
          {b.items.map((it, j) => (
            <div key={j} style={{ display: 'flex', gap: 8, margin: '3px 0' }}>
              <span style={{ color: MUTED, minWidth: 16, textAlign: 'right' }}>
                {b.kind === 'ol' ? `${j + 1}.` : '•'}</span>
              <span style={{ flex: 1 }}><Parts parts={it} /></span>
            </div>
          ))}
        </div>
      ))}
    </>
  )
}

const KEYFRAMES = `
@keyframes dispatch-dots { 0% { content: '' } 25% { content: '.' } 50% { content: '..' } 75% { content: '...' } }
.dispatch-dots::after { content: ''; animation: dispatch-dots 1.2s steps(1) infinite; }
`
