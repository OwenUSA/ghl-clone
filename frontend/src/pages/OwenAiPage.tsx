import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PAGE_BG } from '../components/ai/aiUi'
import { AlertsView } from '../components/owenAi/AlertsView'
import { ProposalsView } from '../components/owenAi/ProposalsView'
import { Conversation } from '../components/owenAi/Conversation'
import { StatusStrip } from '../components/owenAi/StatusStrip'
import { ThreadList } from '../components/owenAi/ThreadList'
import { OWEN_AI_STATUS_KEY, useOwenAiStatus } from '../components/owenAi/useOwenAiStatus'
import { INK, MUTED, NARROW, chipBtn } from '../components/owenAi/tokens'
import {
  ApiError, owenAiAlerts, owenAiApproveProposal, owenAiDismissAlert, owenAiEditThread, owenAiOpenThread,
  owenAiProposals, owenAiRejectProposal, owenAiSend, owenAiThread, owenAiThreads, type OwenAiAlert, type OwenAiAlertStatus, type OwenAiOpenKind, type OwenAiThreadDetail,
} from '../lib/api'
import type { Focus } from '../lib/focus'
import { threadBusy, type OwenAiTab } from '../lib/owenAi'

const THREADS_KEY = ['owen-ai-threads'] as const
const threadKey = (id: number | null) => ['owen-ai-thread', id] as const
const REMEMBER = 'owen-ai:thread'
const ALERTS_KEY = ['owen-ai-alerts'] as const
let handledFocus: number | null = null
const alertsKey = (status: OwenAiAlertStatus) => ['owen-ai-alerts', status] as const
const PROPOSALS_KEY = ['owen-ai-proposals'] as const

/**
 * Owen AI (2026-11-02): the owner's own assistant, on his Claude plan (backend app/owen_ai).
 * Drawn only for the one user GET /api/owen-ai/status answers for — App.tsx and the Sidebar
 * both gate on `useOwenAiStatus().visible`, and the server 404s everyone else anyway.
 *
 * Laid out like the Dispatch assistant: conversations on the left (a drawer on a narrow
 * screen), the chat on the right. An answer is produced by a separate runner, so a sent
 * question comes back QUEUED; the open conversation is polled every 2 s until nothing in it
 * is queued or running, then polling stops.
 *
 * Alerts (2026-11-03): the scheduled audit's findings, behind the left column's Chats | Alerts
 * switch. The open list is polled every 60 s while the page is open (it also feeds the tab's
 * count); a bell alert of kind "owen_ai_issue" lands here through `focus` (App's openRecord)
 * and opens the Alerts view.
 *
 * Proposals (2026-11-09): the Good / Better / Best drafts Owen AI prepared, polled every 30 s
 * (the tab shows how many wait). Approve creates one in Zuper as a DRAFT, never sent; the
 * server answers with the DTR number or Zuper's error. Approve / Reject also post a note into
 * the conversation the draft came from.
 */
export function OwenAiPage({ focus = null }: { focus?: Focus | null } = {}) {
  const qc = useQueryClient()
  const status = useOwenAiStatus({ poll: true })
  const narrow = useNarrow()
  const [drawer, setDrawer] = useState(() => !isNarrowNow())
  const [activeId, setActiveIdState] = useState<number | null>(remembered)
  const [problem, setProblem] = useState<string | null>(null)
  const [sendError, setSendError] = useState<{ id: number; message: string } | null>(null)
  const setActiveId = (id: number | null) => { setActiveIdState(id); remember(id) }
  const [tab, setTab] = useState<OwenAiTab>('chats')
  const [alertStatus, setAlertStatus] = useState<OwenAiAlertStatus>('open')
  const [dismissError, setDismissError] = useState<{ id: number; message: string } | null>(null)
  const [proposalError, setProposalError] = useState<{ id: number; message: string } | null>(null)

  // The bell asked for Owen AI (a new request each time: `n` moves) — show the open alerts.
  // App keeps the last focus, so a request already answered is not replayed on a later visit.
  const focusN = focus?.n
  useEffect(() => {
    if (focusN == null || focusN === handledFocus) return
    handledFocus = focusN
    setTab('alerts')
    setAlertStatus('open')
  }, [focusN])

  const threads = useQuery({
    queryKey: THREADS_KEY, queryFn: () => owenAiThreads(false), enabled: status.visible,
    refetchInterval: (q) => (q.state.data?.some((t) => t.busy) ? 5000 : 60_000),
  })
  const thread = useQuery({
    queryKey: threadKey(activeId), queryFn: () => owenAiThread(activeId as number),
    enabled: status.visible && activeId != null,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
    refetchInterval: (q) => (threadBusy(q.state.data) ? 2000 : false),
  })

  const openAlerts = useQuery({
    queryKey: alertsKey('open'), queryFn: () => owenAiAlerts('open'), enabled: status.visible,
    refetchInterval: 60_000,
  })
  const closedAlerts = useQuery({
    queryKey: alertsKey(alertStatus), queryFn: () => owenAiAlerts(alertStatus),
    enabled: status.visible && tab === 'alerts' && alertStatus !== 'open',
  })
  const shownAlerts = alertStatus === 'open' ? openAlerts : closedAlerts
  const drafts = useQuery({
    queryKey: PROPOSALS_KEY, queryFn: () => owenAiProposals(), enabled: status.visible,
    refetchInterval: tab === 'proposals' ? 10_000 : 30_000,
  })

  // A remembered conversation that is gone opens nothing instead of an error.
  useEffect(() => {
    if (thread.error instanceof ApiError && thread.error.status === 404) setActiveIdState(null)
  }, [thread.error])

  // When an answer lands, the list (its busy dot, its order) and the status strip catch up.
  const busy = threadBusy(thread.data)
  const wasBusy = useRef(false)
  useEffect(() => {
    if (wasBusy.current && !busy) {
      qc.invalidateQueries({ queryKey: THREADS_KEY })
      qc.invalidateQueries({ queryKey: OWEN_AI_STATUS_KEY })
    }
    wasBusy.current = busy
  }, [busy, qc])

  const open = (id: number) => {
    setActiveId(id)
    setProblem(null)
    if (narrow) setDrawer(false)
  }

  const start = useMutation({
    mutationFn: (body: { kind: OwenAiOpenKind; job_number?: string; job_uid?: string }) => owenAiOpenThread(body),
    onMutate: () => setProblem(null),
    onSuccess: (t) => { qc.invalidateQueries({ queryKey: THREADS_KEY }); setTab('chats'); open(t.id) },
    onError: (e) => setProblem((e as Error).message),
  })
  const archive = useMutation({
    mutationFn: (id: number) => owenAiEditThread(id, { archived: true }),
    onSuccess: (_t, id) => {
      if (activeId === id) setActiveId(null)
      qc.removeQueries({ queryKey: threadKey(id) })
      qc.invalidateQueries({ queryKey: THREADS_KEY })
    },
    onError: (e) => setProblem((e as Error).message),
  })
  const dismiss = useMutation({
    mutationFn: (v: { id: number; why: string }) => owenAiDismissAlert(v.id, v.why),
    onMutate: () => setDismissError(null),
    onSuccess: () => qc.invalidateQueries({ queryKey: ALERTS_KEY }),
    onError: (e, v) => setDismissError({ id: v.id, message: (e as Error).message || 'Could not dismiss it.' }),
  })
  const decided = (threadId: number | null) => {
    qc.invalidateQueries({ queryKey: PROPOSALS_KEY })
    if (threadId != null) qc.invalidateQueries({ queryKey: threadKey(threadId) })
  }
  const approve = useMutation({
    mutationFn: (id: number) => owenAiApproveProposal(id),
    onMutate: () => setProposalError(null),
    onSuccess: (p) => {
      decided(p.thread_id)
      if (p.status === 'failed') setProposalError({ id: p.id, message: p.error ?? 'Zuper refused it.' })
    },
    onError: (e, id) => {
      setProposalError({ id, message: (e as Error).message || 'Could not approve it.' })
      qc.invalidateQueries({ queryKey: PROPOSALS_KEY })
    },
  })
  const rejectDraft = useMutation({
    mutationFn: (v: { id: number; why: string }) => owenAiRejectProposal(v.id, v.why),
    onMutate: () => setProposalError(null),
    onSuccess: (p) => decided(p.thread_id),
    onError: (e, v) => setProposalError({ id: v.id, message: (e as Error).message || 'Could not reject it.' }),
  })
  const proposalBusy = approve.isPending ? (approve.variables ?? null)
    : rejectDraft.isPending ? (rejectDraft.variables?.id ?? null) : null
  const openJob = (a: OwenAiAlert) => start.mutate({ kind: 'job', job_uid: a.job_uid,
    ...(a.job_number ? { job_number: a.job_number } : {}) })
  const switchTab = (t: OwenAiTab) => {
    setTab(t)
    setProblem(null)
    if (t !== 'chats' && narrow) setDrawer(false)
  }

  const ask = useMutation({
    mutationFn: (v: { id: number; content: string }) => owenAiSend(v.id, v.content),
    onMutate: () => setSendError(null),
    onSuccess: (m, v) => {
      // The queued question goes straight into the cache, so the thread reads busy and polling starts.
      qc.setQueryData<OwenAiThreadDetail>(threadKey(v.id), (old) => old
        ? { ...old, busy: true, messages: [...old.messages, m] } : old)
      qc.invalidateQueries({ queryKey: threadKey(v.id) })
      qc.invalidateQueries({ queryKey: THREADS_KEY })
    },
    onError: (e, v) => {
      setSendError({ id: v.id, message: (e as Error).message || 'Owen AI could not take the question.' })
      // A 409 can mean "still answering": refresh so the working state shows.
      qc.invalidateQueries({ queryKey: threadKey(v.id) })
    },
  })

  if (!status.visible) return null

  const disabledReason = status.data?.enabled === false
    ? (status.data.sentence ?? 'Owen AI is switched off.') : null
  const title = thread.data?.title

  return (
    <div className="flex min-w-0 flex-1 flex-col" style={{ height: '100vh', overflow: 'hidden', backgroundColor: PAGE_BG }}>
      <style>{KEYFRAMES}</style>
      <div className="flex shrink-0 items-end bg-white px-4"
        style={{ height: 56, borderBottom: '1px solid rgb(234,236,240)', paddingBottom: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)', lineHeight: '24px' }}>Owen AI</div>
      </div>
      <StatusStrip status={status.data} />
      <div className="flex min-h-0 flex-1" style={{ position: 'relative', overflow: 'hidden' }}>
        {drawer && narrow && (
          <div onClick={() => setDrawer(false)} style={{ position: 'absolute', inset: 0,
            background: 'rgba(16,24,40,0.2)', zIndex: 19 }} />
        )}
        {drawer && (
          <ThreadList threads={threads.data ?? []} loading={threads.isLoading}
            error={threads.isError ? (threads.error as Error).message : null} activeId={activeId}
            onOpen={open} onToday={() => start.mutate({ kind: 'daily' })}
            onJob={(n) => start.mutate({ kind: 'job', job_number: n })}
            onNew={() => start.mutate({ kind: 'task' })}
            onArchive={(id) => archive.mutate(id)} opening={start.isPending} problem={problem}
            narrow={narrow} onClose={() => setDrawer(false)}
            tab={tab} onTab={switchTab} openCounts={openAlerts.data?.open_counts}
            pendingProposals={drafts.data?.pending ?? 0} />
        )}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px 0' }}>
            <button type="button" onClick={() => setDrawer(!drawer)} aria-expanded={drawer} style={chipBtn}>
              {drawer ? 'Hide conversations' : 'Conversations'}</button>
            {tab !== 'chats' ? (
              <span style={{ fontSize: 13, color: MUTED }}>{tab === 'alerts' ? 'Alerts' : 'Proposals'}</span>
            ) : title && activeId != null && (
              <span style={{ fontSize: 13, color: MUTED, overflow: 'hidden', textOverflow: 'ellipsis',
                whiteSpace: 'nowrap', minWidth: 0 }}>{title}</span>)}
          </div>
          {tab === 'proposals' ? (
            <ProposalsView proposals={drafts.data?.proposals ?? []} loading={drafts.isLoading}
              error={drafts.isError ? (drafts.error as Error).message : null} sentence={drafts.data?.sentence ?? null}
              onApprove={(p) => approve.mutateAsync(p.id).then((r) => r.status === 'created', () => false)}
              onReject={(p, why) => rejectDraft.mutateAsync({ id: p.id, why }).then(() => true, () => false)}
              busyId={proposalBusy} actionError={proposalError} />
          ) : tab === 'alerts' ? (
            <AlertsView status={alertStatus} onStatus={setAlertStatus}
              alerts={shownAlerts.data?.alerts ?? []} loading={shownAlerts.isLoading}
              error={shownAlerts.isError ? (shownAlerts.error as Error).message : null}
              onOpenJob={openJob} opening={start.isPending}
              onDismiss={(a, why) => dismiss.mutateAsync({ id: a.id, why }).then(() => true, () => false)}
              dismissingId={dismiss.isPending ? (dismiss.variables?.id ?? null) : null}
              dismissError={dismissError} problem={problem} />
          ) : activeId == null ? (
            <div className="flex flex-1 flex-col items-center justify-center" style={{ padding: 24, textAlign: 'center' }}>
              <div style={{ fontSize: 22, color: INK, marginBottom: 8 }}>What do you want to know?</div>
              <div style={{ fontSize: 14, color: MUTED, maxWidth: 420 }}>
                Open <strong>Today</strong> for the day's conversation, <strong>Job…</strong> for one job, or
                start a <strong>New chat</strong>.</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap', justifyContent: 'center' }}>
                <button type="button" disabled={start.isPending} onClick={() => start.mutate({ kind: 'daily' })}
                  style={{ ...chipBtn, fontSize: 13, color: INK, padding: '6px 14px' }}>Today</button>
                <button type="button" disabled={start.isPending} onClick={() => start.mutate({ kind: 'task' })}
                  style={{ ...chipBtn, fontSize: 13, color: INK, padding: '6px 14px' }}>New chat</button>
              </div>
              {problem && <div role="alert" style={{ color: 'rgb(180,35,24)', fontSize: 13, marginTop: 10 }}>{problem}</div>}
            </div>
          ) : (
            <Conversation thread={thread.data} loading={thread.isLoading}
              error={thread.isError && !(thread.error instanceof ApiError && thread.error.status === 404)
                ? (thread.error as Error).message : null}
              onSend={(content) => ask.mutateAsync({ id: activeId, content }).then(() => true, () => false)}
              sending={ask.isPending} sendError={sendError?.id === activeId ? sendError.message : null}
              disabledReason={disabledReason} />
          )}
        </div>
      </div>
    </div>
  )
}

function remembered(): number | null {
  try { return Number(window.localStorage.getItem(REMEMBER)) || null } catch { return null }
}
function remember(id: number | null) {
  try {
    if (id == null) window.localStorage.removeItem(REMEMBER)
    else window.localStorage.setItem(REMEMBER, String(id))
  } catch { /* storage blocked: the page still works, it just forgets */ }
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

const KEYFRAMES = `
@keyframes owen-ai-pulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.3 } }
.owen-ai-pulse { animation: owen-ai-pulse 1.2s ease-in-out infinite; }
`
