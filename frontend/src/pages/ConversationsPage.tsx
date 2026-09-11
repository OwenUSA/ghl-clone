import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { Me } from '../lib/auth'
import { ContactDetailsPanel } from '../components/ContactDetailsPanel'
import {
  IconCalendar, IconChat, IconChevronDown, IconClock, IconEye, IconFilter,
  IconFunnel, IconInbox, IconMail, IconPhone, IconPlay, IconSearch, IconSort,
  IconStar, IconTrash, IconUser, IconUsers,
} from '../components/Icon'
import {
  ApiError, PANE, deleteConversation, listConversations, listEvents,
  patchConversation, placeCall, sendMessage,
  type ConversationSummary, type SendableType, type ThreadEvent,
} from '../lib/api'
import type { Focus } from '../lib/focus'
import { markConversationRead, shouldMarkRead, unreadTabCount } from '../lib/inbox'
import { sendSentence } from '../lib/sendOutcome'

/**
 * Rebuilt from capture/spec.py geometry (captures/conversations, 1440x900).
 * The earlier label-only audit could not see icons or spacing, so this view read
 * as a different product despite matching fonts. Measured values used here:
 *
 *  icon rail      x=232..268, 36x36 buttons, 20x20 icons, y=116 then 48px pitch
 *  "Team inbox"   16px/600 rgb(16,24,40) at x=294 y=118.4
 *  header icons   24x24 at x=522.2 and x=555.2 (filter, sort) — icons, not buttons
 *  tabs           icon 20x20 at y=156, label 14px/500 at y=180, pitch 71.8, w 63.8
 *                 active rgb(16,24,40), inactive rgb(71,84,103)
 *  unread badge   18x15, 10px/600, #fff on rgb(21,112,239), radius 4px
 *  "Select all"   14px/500 rgb(71,84,103) at y=222
 *  row            pitch 89.2; phone 14px/**700** rgb(71,84,103);
 *                 date 12px/400 rgb(102,112,133); count badge on rgb(21,94,239)
 *                 radius 4px; channel label 14px/400 rgb(102,112,133);
 *                 star 14x14 rgb(152,162,179)
 *  thread header  phone 16px/500 rgb(16,24,40); five 24x24 icons from x=852, 40px pitch
 *  date divider   13px/500 rgb(71,84,103) + 16x16 calendar icon
 *  "New" divider  14px/500 rgb(41,112,255)
 *  composer       tray bg #F7F9FD, inner white, radius 4px, height 40
 */
const TABS = [
  { key: 'unread', label: 'Unread', Icon: IconMail },
  { key: 'all', label: 'All', Icon: IconInbox },
  { key: 'recent', label: 'Recent', Icon: IconClock },
  { key: 'starred', label: 'Starred', Icon: IconStar },
] as const

const RAIL = [
  { Icon: IconChat, label: 'Conversations' },
  { Icon: IconSearch, label: 'Search' },
  { Icon: IconUser, label: 'Assigned to me' },
  { Icon: IconUsers, label: 'Team inbox' },
  { Icon: IconFunnel, label: 'Filters' },
  { Icon: IconEye, label: 'Unread only' },
]

const SORTS = [
  { key: 'latest', label: 'Latest - All Messages' },
  { key: 'oldest', label: 'Oldest - All Messages' },
  { key: 'latest_manual', label: 'Latest - Manual Messages' },
  { key: 'oldest_manual', label: 'Oldest - Manual Messages' },
  { key: 'sla_overdue', label: 'Longest SLA Overdue' },
  { key: 'sla_next', label: 'Next SLA Target' },
]

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'conversations', label: 'Conversations' },
  { key: 'activities', label: 'Activities' },
  { key: 'sms', label: 'SMS' },
  { key: 'call', label: 'Call' },
  { key: 'whatsapp', label: 'WhatsApp', unimplemented: true },
  { key: 'internal_comment', label: 'Internal Comment' },
  { key: 'contact', label: 'Contacts' },
  { key: 'appointment', label: 'Appointments' },
  { key: 'opportunity', label: 'Opportunities' },
  { key: 'payment', label: 'Payments' },
  { key: 'invoice', label: 'Invoice' },
  { key: 'ai_logs', label: 'AI Action Logs', unimplemented: true },
  { key: 'sla', label: 'SLA', unimplemented: true },
  { key: 'wa_perm', label: 'WhatsApp Permission', unimplemented: true },
]

const dayLabel = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
const timeLabel = (iso: string) =>
  new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
const fmtDur = (s: number) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

function IconBtn({
  children, title, onClick, size = 24, disabled = false,
}: {
  children: React.ReactNode
  title: string
  onClick?: () => void
  size?: number
  /**
   * A control a role may not use renders DISABLED with a title saying why, never
   * as a form that 403s on submit -- the precedent set by d1f7c50 / b943f4b and
   * followed by the Internal Comment row three inches away. Only the cursor and
   * the opacity change, so the measured 24x24 geometry is untouched.
   */
  disabled?: boolean
}) {
  return (
    <button
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
      className="flex items-center justify-center"
      style={{
        width: size, height: size, color: 'rgb(71,84,103)',
        ...(disabled ? { opacity: 0.4, cursor: 'not-allowed' } : {}),
      }}
    >
      {children}
    </button>
  )
}

function Dropdown({
  items, value, onPick, onClose, right = false, up = false,
}: {
  items: { key: string; label: string; unimplemented?: boolean; blocked?: string }[]
  value: string
  onPick: (k: string) => void
  onClose: () => void
  right?: boolean
  /**
   * Open upward. The composer's channel picker sits on the last 40px of the
   * viewport, so a menu dropped below it renders outside the thread pane and is
   * clipped away entirely -- the same class of bug as the missing `top` below,
   * at the other end of the window.
   */
  up?: boolean
}) {
  return (
    <div
      role="menu"
      className={`absolute z-30 bg-white ${up ? 'mb-1' : 'mt-1'}`}
      style={{
        // `top` must be set explicitly. Without it the menu falls back to its static
        // position, and its `relative` parent is a flex row with `items-center`, so the
        // menu is vertically CENTRED on the 24px icon row and overflows upward off the
        // top of the window. 100% drops it below the row, where `mt-1` spaces it.
        // Written as two literal properties rather than one computed key, so the
        // anchoring stays greppable — test_frontend_layout reads this style block.
        top: up ? undefined : '100%',
        bottom: up ? '100%' : undefined,
        [right ? 'right' : 'left']: 0,
        minWidth: 224,
        borderRadius: 8,
        border: '1px solid rgb(234,236,240)',
        boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)',
        padding: 4,
      } as React.CSSProperties}
    >
      {items.map((it) => {
        // Two reasons a row can be dead, and they say different things. Either
        // way it renders disabled rather than absent -- the precedent set by Add
        // Contact and the Actions tab (DECISIONS.md): a gap the user can see
        // beats a menu that quietly gets shorter, and beats clicking into a 403.
        const off = it.unimplemented || Boolean(it.blocked)
        return (
          <button
            key={it.key}
            role="menuitem"
            disabled={off}
            title={it.blocked
              ?? (it.unimplemented ? 'Present in GHL, not implemented in v1' : undefined)}
            onClick={() => { if (!off) { onPick(it.key); onClose() } }}
            className="block w-full text-left"
            style={{
              padding: '8px 12px',
              borderRadius: 6,
              fontSize: 14,
              color: off ? 'rgb(152,162,179)' : 'rgb(52,64,84)',
              backgroundColor: value === it.key ? 'rgb(239,244,255)' : 'transparent',
              cursor: off ? 'not-allowed' : 'pointer',
            }}
          >
            {it.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * A call row used to say "Call completed" unconditionally, which was true of every
 * call in the database while the only ones were inbound records the feed had already
 * closed out. It is not true now: the thread can place an outbound call, and that
 * call has no outcome until it has one. A missed call labelled "completed" is
 * precisely the kind of wrong the owner would catch before we did.
 */
const CALL_TONE: Record<string, string> = {
  completed: 'rgb(18,183,106)',
  'no-answer': 'rgb(181,71,8)',
  busy: 'rgb(181,71,8)',
  voicemail: 'rgb(102,112,133)',
  failed: 'rgb(180,35,24)',
}

const CALL_WORD: Record<string, string> = {
  completed: 'completed',
  'no-answer': 'no answer',
  busy: 'busy',
  voicemail: 'went to voicemail',
  failed: 'failed',
}

function callLabel(e: ThreadEvent): string {
  const way = e.direction === 'INBOUND' ? 'Inbound call' : 'Outbound call'
  const word = e.call_status ? CALL_WORD[e.call_status] ?? e.call_status : null
  return word ? `${way} ${word}` : way
}

/**
 * How each delivery state reads under a message, and in what colour.
 *
 * The owner's requirement was "i want to know if the text arrived or not", so the
 * six states have to stay six states on the screen:
 *
 *  - QUEUED/SENT are on the way and not yet confirmed — grey, no alarm.
 *  - DELIVERED is the only one that means it arrived — green, and the only one
 *    allowed to say so.
 *  - FAILED and REFUSED both mean it did not arrive, and they are kept apart
 *    because the answer differs: FAILED might work on a retry, REFUSED will not
 *    until something is switched on. Both carry `delivery_detail` beside them.
 *  - LOGGED_ONLY is the stub transport. It keeps saying "not sent" in plain words
 *    so a recorded-only message can never be mistaken for a real one sitting in the
 *    same thread as real ones.
 *
 * An unknown status (a state added on the server before this file catches up) falls
 * through to the raw word rather than being hidden — an unexplained label is a
 * question someone asks; a silently dropped one is a message that looks delivered.
 */
const DELIVERY: Record<string, { label: string; color: string }> = {
  QUEUED: { label: 'queued', color: 'rgb(102,112,133)' },
  SENT: { label: 'sent', color: 'rgb(102,112,133)' },
  DELIVERED: { label: 'delivered', color: 'rgb(2,122,72)' },
  FAILED: { label: 'failed', color: 'rgb(180,35,24)' },
  REFUSED: { label: 'not sent', color: 'rgb(181,71,8)' },
  LOGGED_ONLY: { label: 'not sent (recorded only)', color: 'rgb(102,112,133)' },
  PENDING: { label: 'pending', color: 'rgb(102,112,133)' },
}

function DeliveryNote({ e }: { e: ThreadEvent }) {
  // Inbound messages and internal notes are delivered to nobody, so they carry no
  // status and get no line.
  if (!e.delivery_status) return null
  const known = DELIVERY[e.delivery_status]
  const label = known?.label ?? e.delivery_status.toLowerCase().replace(/_/g, ' ')
  const color = known?.color ?? 'rgb(102,112,133)'
  return (
    <>
      <span style={{ color }}> · {label}</span>
      {e.delivery_detail && (
        // The reason gets its own line rather than being appended to the timestamp
        // row: these are whole sentences ("the number is still waiting on carrier
        // approval"), and squeezing one onto the end of "10:42 AM · not sent"
        // produces a line nobody finishes reading.
        <div style={{ color, marginTop: 2, maxWidth: 460 }}>{e.delivery_detail}</div>
      )}
    </>
  )
}

function EventBubble({ e }: { e: ThreadEvent }) {
  const inbound = e.direction === 'INBOUND'
  const isActivity = !['SMS', 'EMAIL', 'CALL', 'INTERNAL_COMMENT'].includes(e.type)

  if (isActivity) {
    return (
      <div className="my-3 flex justify-center">
        <div style={{
          fontSize: 12, color: 'rgb(102,112,133)',
          backgroundColor: 'rgb(242,244,247)', borderRadius: 16, padding: '4px 12px',
        }}>
          {e.body} · {timeLabel(e.occurred_at)}
        </div>
      </div>
    )
  }

  return (
    <div className={`my-2 flex items-end gap-2 ${inbound ? 'justify-start' : 'justify-end'}`}>
      {inbound && (
        <div
          className="flex shrink-0 items-center justify-center"
          style={{
            width: 28, height: 28, borderRadius: '50%',
            backgroundColor: 'rgb(185,230,254)', fontSize: 11,
            color: 'rgb(71,84,103)',
          }}
        >
          +1
        </div>
      )}
      <div>
        <div style={{
          maxWidth: 460, borderRadius: 8, padding: '10px 12px', fontSize: 14,
          backgroundColor: inbound ? 'rgb(242,244,247)' : 'rgb(239,244,255)',
          color: 'rgb(52,64,84)',
        }}>
          {e.type === 'CALL' ? (
            <div>
              {/* measured: "Call completed" 14px/400 rgb(16,24,40), then a player
                  row with play, waveform, 0:00 / 0:13, 1x, volume, download */}
              <div className="flex items-center gap-1"
                style={{ fontSize: 14, fontWeight: 400, color: 'rgb(16,24,40)' }}>
                <IconPhone size={14} color={CALL_TONE[e.call_status ?? ''] ?? 'rgb(102,112,133)'} />
                {callLabel(e)}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <IconPlay size={26} color="rgb(21,112,239)" />
                <span style={{ letterSpacing: -1, color: 'rgb(152,162,179)', fontSize: 12 }}>
                  ▁▃▅▂▇▃▅▁▆▂▄▁▃
                </span>
                <span style={{ fontSize: 12, color: 'rgb(71,84,103)' }}>
                  0:00 / {e.duration_seconds != null ? fmtDur(e.duration_seconds) : '0:00'}
                </span>
                <span style={{ fontSize: 12, color: 'rgb(71,84,103)' }}>1x</span>
              </div>
              <audio controls src={e.recording_url ?? undefined}
                style={{ marginTop: 6, height: 28, width: 280 }} />
            </div>
          ) : (
            <div>{e.body}</div>
          )}
        </div>
        <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 4 }}>
          {timeLabel(e.occurred_at)}
          <DeliveryNote e={e} />
        </div>
      </div>
    </div>
  )
}

export function ConversationsPage({ user, focus }: { user: Me; focus?: Focus | null }) {
  const [tab, setTab] = useState<string>('all')
  const [sort, setSort] = useState('latest')
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [showSort, setShowSort] = useState(false)
  const [showFilter, setShowFilter] = useState(false)
  const [rail, setRail] = useState(0)
  const [checked, setChecked] = useState<number[]>([])
  const [composerType, setComposerType] = useState<SendableType>('SMS')
  const [showComposerType, setShowComposerType] = useState(false)
  const [sending, setSending] = useState(false)
  // One place for whatever the last action wants to tell the operator: a refused
  // send, a call that is ringing, a suppression. Rendered above the composer.
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null)
  const [calling, setCalling] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  // Is this tab in front? A thread left open on a background tab must keep its
  // badge when a text arrives -- nobody read it. See `shouldMarkRead`.
  const [visible, setVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState === 'visible')
  // The (conversation, count) pair already asked about, so an effect that re-runs
  // for an unrelated reason does not fire a second PATCH, and a REFUSED one is
  // not retried every ten seconds against a server that has already said no.
  const asked = useRef<string | null>(null)
  const qc = useQueryClient()

  // Internal notes are STAFF-only as of 2026-09-10 (DECISIONS.md), and the
  // backend answers a TECH's ?filter=internal_comment with 403. Disable the row
  // rather than let them pick it and read an error.
  const filters =
    user.role === 'TECH'
      ? FILTERS.map((f) =>
          f.key === 'internal_comment'
            ? { ...f, blocked: 'Internal notes are staff-only' }
            : f,
        )
      : FILTERS

  // The ctrl+K palette asked for one record. Opening it here, rather than
  // teaching the palette how each page's detail panel works, keeps a searched
  // record and a clicked row on exactly the same path.
  useEffect(() => {
    if (focus) setSelected(focus.id)
  }, [focus])

  /**
   * Inbound activity has to appear without a manual reload, and POLLING is the
   * mechanism — deliberately, not for lack of alternatives.
   *
   * A websocket or SSE would be the reflex, and both are the wrong trade here: the
   * app is four users in one office, it sits behind the shared Traefik with no
   * websocket route configured, and the API is a synchronous FastAPI app with no
   * pub/sub of any kind. A push channel would mean new infrastructure on the server
   * and a reconnect/backoff state machine in the client, to deliver rows that a
   * `SELECT` already returns in single-digit milliseconds against a 13-contact
   * database.
   *
   * Ten seconds is the compromise: a missed call's auto-text-back and the inbound
   * SMS after it land while the dispatcher is still looking at the thread, and the
   * cost is six requests a minute per open tab. `refetchOnWindowFocus` is what
   * actually covers the common case — coming back to the tab shows current data
   * immediately rather than up to ten seconds late.
   */
  const LIVE = { refetchInterval: 10_000, refetchOnWindowFocus: true } as const

  const convs = useQuery({
    queryKey: ['conversations', tab, sort],
    queryFn: () => listConversations(tab, sort),
    ...LIVE,
  })
  const active = selected ?? convs.data?.[0]?.id ?? null
  const events = useQuery({
    queryKey: ['events', active, filter],
    queryFn: () => listEvents(active as number, filter),
    enabled: active != null,
    ...LIVE,
  })
  const current: ConversationSummary | undefined = convs.data?.find((c) => c.id === active)

  useEffect(() => {
    const on = () => setVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])

  /**
   * What the thread was holding when it was opened, captured BEFORE the badge is
   * cleared.
   *
   * The measured "New" divider is drawn from the unread count, so clearing the
   * count on open would delete a measured element from the screen by a side
   * door: the divider would exist only in the instant before the PATCH came
   * back. The snapshot keeps it for as long as the thread stays open, which is
   * also what it means -- "this is where you were up to".
   */
  const [openedWith, setOpenedWith] = useState<{ id: number; unread: number } | null>(null)
  useEffect(() => {
    if (active == null) { setOpenedWith(null); return }
    // Wait for the row: on a cold load `current` is undefined for a frame, and a
    // snapshot of 0 taken then would stick for the whole visit.
    if (!current) return
    setOpenedWith((s) =>
      s && s.id === active ? s : { id: active, unread: current.unread_count })
  }, [active, current])

  /**
   * Mark one thread read: ask the server, and only then clear the badge.
   *
   * `update.apply` is applied to EVERY cached conversation list rather than just
   * the one on screen -- the cache is keyed by (tab, sort), so the Unread tab's
   * copy of this row would otherwise still be carrying the old count when the
   * user switches to it. Nothing else is invalidated; the list refetch below is
   * the only request this triggers beyond the PATCH itself.
   */
  const markRead = async (id: number): Promise<boolean> => {
    const update = await markConversationRead(
      id, (cid) => patchConversation(cid, { read: true }))
    qc.setQueriesData<ConversationSummary[]>({ queryKey: ['conversations'] }, update.apply)
    if (update.error) {
      // The badge stays where it was -- `apply` is identity on a failure -- and
      // the operator is told, rather than being shown a cleared badge for a
      // thread the server still considers unread.
      setNote({ text: update.error.message, bad: true })
      return false
    }
    void qc.invalidateQueries({ queryKey: ['conversations'] })
    return true
  }

  /**
   * Opening a thread marks it read. So does a new message ARRIVING on a thread
   * that is already open and in front of somebody.
   *
   * That second half is a decision, and the alternative was to let the badge come
   * back: the poll raises `unread_count` and the row lights up again while the
   * dispatcher is looking straight at the message. "Unread" has to mean "nobody
   * has seen this", and somebody has -- it is on their screen, ten seconds after
   * it landed. A badge that reappears on the thread you are reading is the same
   * complaint the owner already made, arriving by a different route.
   *
   * The guard against that being wrong is `visible`: on a background tab the
   * badge stays and clears when the tab comes forward.
   *
   * There is no hover handler and no keyboard navigation in this list, so
   * "opened" and "selected" are the same event and nothing else can trigger
   * this. The first row is auto-selected when nothing is chosen, and that counts
   * as opening -- its thread is rendered in full beside the list, so a badge on
   * it would be claiming nobody had seen messages that are on screen.
   */
  const openUnread = current?.unread_count ?? 0
  useEffect(() => {
    if (!shouldMarkRead(active, openUnread, visible)) return
    const key = `${active}:${openUnread}`
    if (asked.current === key) return
    asked.current = key
    void markRead(active as number)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, openUnread, visible])

  // Switching threads must not carry a half-answered "are you sure" across to
  // the next one -- the same rule the contact panel's delete follows.
  useEffect(() => {
    setConfirmDelete(false)
    setDeleteError(null)
  }, [active])

  // `DELETE /api/conversations/{id}` is ADMIN. Mirror it here so the other two
  // roles are never offered a control that can only answer 403.
  const canDelete = user.role === 'ADMIN'

  const onDelete = async () => {
    if (active == null || deleting) return
    setDeleting(true)
    setDeleteError(null)
    try {
      const r = await deleteConversation(active)
      setConfirmDelete(false)
      // Close the thread first, then drop the row from every cached list, so the
      // pane is never rendering a conversation that no longer exists.
      setSelected(null)
      setChecked((s) => s.filter((x) => x !== r.deleted))
      qc.setQueriesData<ConversationSummary[]>(
        { queryKey: ['conversations'] },
        (rows) => (rows ?? []).filter((c) => c.id !== r.deleted))
      // Its timeline is gone too; invalidating instead would refetch a 404 over
      // a pane that is on its way out.
      qc.removeQueries({ queryKey: ['events', r.deleted] })
      void qc.invalidateQueries({ queryKey: ['conversations'] })
      setNote(null)
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.message : 'The conversation could not be deleted.')
    } finally {
      setDeleting(false)
    }
  }

  // Internal notes are STAFF-only (DECISIONS.md, 2026-09-10) and the backend
  // refuses a TECH's write with 403. The same rule that disables the filter row
  // disables the composer's Internal Comment mode.
  const canWriteInternal = user.role !== 'TECH'
  const composerModes = [
    { key: 'SMS', label: 'SMS' },
    {
      key: 'INTERNAL_COMMENT',
      label: 'Internal Comment',
      blocked: canWriteInternal ? undefined : 'Internal notes are staff-only',
    },
  ]

  /**
   * Why Send is dead, or null when it is live.
   *
   * Every one of these is a condition the SERVER also enforces -- this is not the
   * gate, it is the explanation. Letting someone type a message and only then
   * discover it was never going anywhere is the failure this avoids, and it is the
   * same precedent as the disabled Internal Comment row rather than a missing one.
   */
  const sendBlocked = (): string | null => {
    if (!current) return 'No conversation selected.'
    if (composerType === 'INTERNAL_COMMENT') {
      return canWriteInternal ? null : 'Internal notes are staff-only.'
    }
    if (!current.contact_phone) return 'This contact has no phone number.'
    if (current.contact_dnd) return 'This contact is on Do Not Disturb.'
    return null
  }
  const blocked = sendBlocked()

  const onSend = async () => {
    const text = draft.trim()
    if (!text || blocked || active == null || sending) return
    setSending(true)
    setNote(null)
    try {
      const r = await sendMessage(active, text, composerType)
      // The draft is cleared for anything that was RECORDED -- including a refusal,
      // which writes a row carrying the text. It is kept only when nothing was
      // written at all (a suppression), so the operator does not have to retype a
      // message that never existed.
      if (!r.suppressed) setDraft('')
      // "queued" and "recorded" are the only two outcomes where the message is
      // actually on its way (or was never meant to leave). Everything else -- a
      // refusal, a failure, a suppression, and the stub transport's "sent" --
      // means it did not go, and is shown as a problem rather than a confirmation.
      const went = r.reason === 'queued' || r.reason === 'recorded'
      setNote({ text: sendSentence(r.reason), bad: !went })
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['events', active] }),
        qc.invalidateQueries({ queryKey: ['conversations'] }),
      ])
    } catch (err) {
      setNote({
        text: err instanceof ApiError ? err.message : 'The message could not be sent.',
        bad: true,
      })
    } finally {
      setSending(false)
    }
  }

  const onCall = async () => {
    if (active == null || calling) return
    setCalling(true)
    setNote(null)
    try {
      const r = await placeCall(active)
      setNote({ text: r.reason, bad: !r.placed })
      if (r.placed) {
        await Promise.all([
          qc.invalidateQueries({ queryKey: ['events', active] }),
          qc.invalidateQueries({ queryKey: ['conversations'] }),
        ])
      }
    } catch (err) {
      setNote({
        text: err instanceof ApiError ? err.message : 'The call could not be placed.',
        bad: true,
      })
    } finally {
      setCalling(false)
    }
  }

  // The badge over the Unread TAB counts CONVERSATIONS, because that is what the
  // tab filters. Summing unread messages made it read "2" above a list of one
  // row. The per-row badge further down keeps the message total -- there it
  // labels a single thread, so "2" means two unread texts, which is true.
  const unreadTotal = unreadTabCount(convs.data)
  const allChecked = (convs.data ?? []).length > 0 && checked.length === convs.data!.length

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col" style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div className="flex shrink-0 items-center gap-6 bg-white px-4"
        style={{ height: 90, borderBottom: '1px solid rgb(234,236,240)' }}>
        <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)' }}>Conversations</div>
        {['Conversations', 'Manual Actions', 'Snippets', 'Trigger Links', 'Analytics', 'Settings']
          .map((t, i) => (
            <div key={t} style={{
              fontSize: 14, fontWeight: 500, lineHeight: '25.6px',
              color: i === 0 ? 'rgb(56,160,219)' : 'rgb(75,85,99)',
            }}>{t}</div>
          ))}
      </div>

      <div className="flex min-h-0 flex-1">
        {/* icon rail — measured 36x36 buttons at x=232, 20x20 icons, 48px pitch */}
        <div className="flex shrink-0 flex-col items-center gap-3 pt-3"
          style={{ width: PANE.rail }}>
          {RAIL.map((r, i) => (
            <button
              key={r.label}
              title={r.label}
              onClick={() => setRail(i)}
              className="flex items-center justify-center"
              style={{
                width: 36, height: 36, borderRadius: 6,
                backgroundColor: rail === i ? 'rgb(0,78,235)' : 'transparent',
                color: rail === i ? '#fff' : 'rgb(52,64,84)',
              }}
            >
              <r.Icon size={20} color={rail === i ? '#fff' : 'rgb(52,64,84)'} />
            </button>
          ))}
        </div>

        <div className="flex min-h-0 flex-1 gap-3 pb-3 pr-3">
          {/* inbox list */}
          <div className="flex shrink-0 flex-col overflow-hidden bg-white"
            style={{ width: PANE.list, borderRadius: 8, border: '1px solid rgb(234,236,240)' }}>
            <div className="flex shrink-0 items-center px-3 pt-3">
              <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>
                Team inbox
              </div>
              <div className="relative ml-auto flex items-center gap-2">
                <IconBtn title="Filter conversations" onClick={() => setShowFilter(false)}>
                  <IconFilter size={20} color="rgb(71,84,103)" />
                </IconBtn>
                <IconBtn title="Sort conversations" onClick={() => setShowSort((s) => !s)}>
                  <IconSort size={20} color="rgb(71,84,103)" />
                </IconBtn>
                {showSort && (
                  <Dropdown items={SORTS} value={sort} onPick={setSort} right
                    onClose={() => setShowSort(false)} />
                )}
              </div>
            </div>

            {/* tabs: icon above label, 71.8px pitch, badge on Unread */}
            <div className="flex shrink-0 px-2 pt-3">
              {TABS.map((t) => {
                const on = tab === t.key
                return (
                  <button key={t.key} onClick={() => setTab(t.key)}
                    className="relative flex flex-1 flex-col items-center justify-center"
                    style={{ paddingBottom: 8 }}>
                    <div className="relative">
                      <t.Icon size={20} color={on ? 'rgb(16,24,40)' : 'rgb(102,112,133)'} />
                      {t.key === 'unread' && unreadTotal > 0 && (
                        <span style={{
                          position: 'absolute', top: -6, left: 14,
                          minWidth: 18, height: 15, padding: '0 4px',
                          fontSize: 10, fontWeight: 600, color: '#fff',
                          backgroundColor: 'rgb(21,112,239)', borderRadius: 4,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                        }}>{unreadTotal}</span>
                      )}
                    </div>
                    <span style={{
                      marginTop: 4, fontSize: 14, fontWeight: 500,
                      color: on ? 'rgb(16,24,40)' : 'rgb(71,84,103)',
                    }}>{t.label}</span>
                    {on && (
                      <span style={{
                        position: 'absolute', bottom: 0, left: 6, right: 6, height: 2,
                        backgroundColor: 'rgb(0,78,235)',
                      }} />
                    )}
                  </button>
                )
              })}
            </div>

            {/* Select all — measured 14px/500 rgb(71,84,103) */}
            <label className="flex shrink-0 items-center gap-2 px-3"
              style={{ height: 36, borderTop: '1px solid rgb(242,244,247)' }}>
              <input
                type="checkbox"
                checked={allChecked}
                onChange={(e) =>
                  setChecked(e.target.checked ? (convs.data ?? []).map((c) => c.id) : [])}
              />
              <span style={{ fontSize: 14, fontWeight: 500, color: 'rgb(71,84,103)' }}>
                Select all
              </span>
            </label>

            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
              {convs.isLoading && <div className="p-3" style={{ fontSize: 14 }}>Loading…</div>}
              {convs.data?.length === 0 && (
                <div className="p-3" style={{ fontSize: 14 }}>No conversations in {tab}.</div>
              )}
              {convs.data?.map((c) => {
                const on = c.id === active
                return (
                  <div
                    key={c.id}
                    onClick={() => setSelected(c.id)}
                    className="cursor-pointer"
                    style={{
                      display: 'flex', gap: 8, padding: '10px 8px', marginTop: 6,
                      borderRadius: 8,
                      border: on ? '1px solid rgb(0,78,235)' : '1px solid transparent',
                      backgroundColor: on ? 'rgb(247,249,253)' : 'transparent',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked.includes(c.id)}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) =>
                        setChecked((s) =>
                          e.target.checked ? [...s, c.id] : s.filter((x) => x !== c.id))}
                      style={{ marginTop: 3 }}
                    />
                    <div className="relative shrink-0">
                      <div className="flex items-center justify-center"
                        style={{
                          width: 26, height: 26, borderRadius: '50%',
                          backgroundColor: 'rgb(185,230,254)', fontSize: 10,
                          color: 'rgb(71,84,103)',
                        }}>
                        {(c.contact_name ?? '+1').slice(0, 2)}
                      </div>
                      <span style={{
                        position: 'absolute', right: -4, bottom: -2,
                        color: 'rgb(107,114,128)',
                      }}>
                        <IconPhone size={14} color="rgb(107,114,128)" />
                      </span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start">
                        <span className="truncate" style={{
                          fontSize: 14, fontWeight: 700, color: 'rgb(71,84,103)',
                        }}>
                          {c.contact_name ?? c.contact_phone}
                        </span>
                        <span className="ml-auto flex items-center gap-1 pl-2">
                          <span style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
                            {dayLabel(c.last_event_at)}
                          </span>
                          {c.unread_count > 0 && (
                            <span style={{
                              minWidth: 17, height: 20, padding: '0 5px', fontSize: 12,
                              color: '#fff', backgroundColor: 'rgb(21,94,239)',
                              borderRadius: 4, display: 'flex', alignItems: 'center',
                              justifyContent: 'center',
                            }}>{c.unread_count}</span>
                          )}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center">
                        <span style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
                          {c.contact_phone}
                        </span>
                        <span className="ml-auto">
                          <IconStar size={14} color="rgb(152,162,179)" />
                        </span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* thread */}
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white"
            style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)' }}>
            <div className="flex shrink-0 items-center gap-2 px-4"
              style={{ height: 56, borderBottom: '1px solid rgb(234,236,240)' }}>
              <div className="flex items-center justify-center shrink-0"
                style={{
                  width: 28, height: 28, borderRadius: '50%',
                  backgroundColor: 'rgb(185,230,254)', fontSize: 11,
                  color: 'rgb(71,84,103)',
                }}>+1</div>
              <div style={{ fontSize: 16, fontWeight: 500, color: 'rgb(16,24,40)' }}>
                {current?.contact_name ?? current?.contact_phone ?? '—'}
              </div>
              {/* five 24x24 icons at a 40px pitch — measured x=852..1036 */}
              <div className="relative ml-auto flex items-center" style={{ gap: 16 }}>
                <IconBtn title="Filter messages" onClick={() => setShowFilter((s) => !s)}>
                  <span className="flex items-center">
                    <IconChat size={24} color="rgb(71,84,103)" />
                    <IconChevronDown size={16} color="rgb(29,41,57)" />
                  </span>
                </IconBtn>
                {/* Click-to-call. owen-main rings an operator's handset first and
                    then the customer, and bridges them -- so this places a real
                    call without a browser softphone. */}
                <IconBtn
                  title={calling
                    ? 'Placing the call…'
                    : 'Call %s'.replace('%s', current?.contact_phone ?? 'this contact')}
                  onClick={() => void onCall()}
                >
                  <IconPhone size={24}
                    color={calling ? 'rgb(152,162,179)' : 'rgb(71,84,103)'} />
                </IconBtn>
                <IconBtn title="Add to Favorites"><IconStar size={24} color="rgb(71,84,103)" /></IconBtn>
                {/* Was decorative. It now runs exactly the same PATCH that opening
                    the thread does, so the two can never disagree about what
                    "read" means. */}
                <IconBtn
                  title="Mark as read"
                  disabled={active == null}
                  onClick={() => {
                    if (active == null) return
                    // Clear the guard: this is a person asking, so it must go to
                    // the server even if the same (thread, count) was tried and
                    // refused a moment ago.
                    asked.current = null
                    void markRead(active)
                  }}
                >
                  <IconMail size={24} color="rgb(71,84,103)" />
                </IconBtn>
                <IconBtn
                  title={canDelete
                    ? 'Delete Conversation'
                    : 'Only an admin can delete a conversation'}
                  disabled={!canDelete || active == null}
                  onClick={() => { setDeleteError(null); setConfirmDelete(true) }}
                >
                  <IconTrash size={24}
                    color={canDelete ? 'rgb(71,84,103)' : 'rgb(152,162,179)'} />
                </IconBtn>
                {showFilter && (
                  <Dropdown items={filters} value={filter} onPick={setFilter} right
                    onClose={() => setShowFilter(false)} />
                )}
              </div>
            </div>

            {/* The confirmation names the contact and how much correspondence is
                about to go. A bare "are you sure" tells the person nothing they
                did not already know, and this is irreversible: there is no soft
                delete anywhere in this codebase (DECISIONS.md). */}
            {confirmDelete && current && (
              <div
                role="dialog"
                aria-label="Delete conversation"
                className="shrink-0"
                style={{
                  margin: 12, padding: 12, borderRadius: 8,
                  backgroundColor: 'rgb(254,243,242)',
                  border: '1px solid rgb(253,162,155)',
                }}
              >
                <div style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>
                  Delete the conversation with{' '}
                  <strong>{current.contact_name ?? current.contact_phone ?? 'this contact'}</strong>
                  {' '}and all {current.event_count}{' '}
                  {current.event_count === 1 ? 'message' : 'messages'} on it —
                  texts, calls, recordings and internal notes? This cannot be undone.
                </div>
                <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 6 }}>
                  The contact, their opportunities and their appointments are kept.
                  Only this thread is removed.
                </div>
                {deleteError && (
                  <div role="alert" style={{
                    marginTop: 8, fontSize: 13, color: 'rgb(180,35,24)',
                  }}>{deleteError}</div>
                )}
                <div className="mt-3 flex gap-2">
                  <button
                    onClick={() => { setConfirmDelete(false); setDeleteError(null) }}
                    style={{
                      height: 30, padding: '0 12px', borderRadius: 6, fontSize: 13,
                      fontWeight: 500, color: 'rgb(52,64,84)', backgroundColor: '#fff',
                      border: '1px solid rgb(234,236,240)',
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => void onDelete()}
                    disabled={deleting}
                    style={{
                      height: 30, padding: '0 12px', borderRadius: 6, fontSize: 13,
                      fontWeight: 500, color: '#fff', backgroundColor: 'rgb(180,35,24)',
                      opacity: deleting ? 0.6 : 1,
                    }}
                  >
                    {deleting ? 'Deleting…' : 'Delete conversation'}
                  </button>
                </div>
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
              {events.isLoading && <div style={{ fontSize: 14 }}>Loading…</div>}
              {events.data?.length === 0 && (
                <div style={{ fontSize: 14, color: 'rgb(102,112,133)', padding: 16 }}>
                  Nothing matches “{filters.find((f) => f.key === filter)?.label}”.
                </div>
              )}
              {events.data && events.data.length > 0 && (
                <>
                  {/* date divider — measured 13px/500 rgb(71,84,103) + calendar icon */}
                  <div className="my-3 flex items-center justify-center gap-1">
                    <IconCalendar size={16} color="rgb(71,84,103)" />
                    <span style={{ fontSize: 13, fontWeight: 500, color: 'rgb(71,84,103)' }}>
                      {dayLabel(events.data[0].occurred_at)}
                    </span>
                  </div>
                  {/* unread divider — measured 14px/500 rgb(41,112,255).
                      Drawn from the snapshot taken when the thread was opened,
                      not from the live count: opening now clears that count, and
                      reading it here would make the divider flash and vanish. */}
                  {(openedWith?.unread ?? 0) > 0 && (
                    <div className="flex items-center gap-2">
                      <span style={{ fontSize: 14, fontWeight: 500, color: 'rgb(41,112,255)' }}>
                        New
                      </span>
                      <span className="flex-1" style={{ borderTop: '1px solid rgb(41,112,255)' }} />
                    </div>
                  )}
                </>
              )}
              {events.data?.map((e) => <EventBubble key={e.id} e={e} />)}
            </div>

            {/* composer — measured tray #F7F9FD, inner white radius 4, height 40 */}
            <div className="shrink-0" style={{ backgroundColor: '#F7F9FD', padding: 8 }}>
              {note && (
                <div
                  role="status"
                  style={{
                    marginBottom: 6, padding: '6px 10px', borderRadius: 4, fontSize: 13,
                    color: note.bad ? 'rgb(180,35,24)' : 'rgb(2,122,72)',
                    backgroundColor: note.bad ? 'rgb(254,243,242)' : 'rgb(236,253,243)',
                    border: '1px solid '
                      + (note.bad ? 'rgb(254,205,202)' : 'rgb(171,239,198)'),
                  }}
                >
                  {note.text}
                </div>
              )}
              {blocked && composerType === 'SMS' && (
                <div style={{ marginBottom: 6, fontSize: 13, color: 'rgb(181,71,8)' }}>
                  {blocked}
                </div>
              )}
              <div className="flex items-center"
                style={{
                  height: 40, borderRadius: 4, backgroundColor: '#fff',
                  border: '1px solid rgb(234,236,240)',
                }}>
                {/* The channel picker. It rendered as decoration before there was
                    anything to pick; it now chooses what the composer writes. */}
                <div className="relative flex shrink-0 items-center justify-center"
                  style={{ width: 54 }}>
                  <button
                    title={composerType === 'SMS' ? 'Sending as SMS' : 'Writing an internal note'}
                    aria-label="Message type"
                    onClick={() => setShowComposerType((s) => !s)}
                    className="flex items-center gap-1"
                  >
                    <IconChat size={16}
                      color={composerType === 'SMS' ? 'rgb(21,112,239)' : 'rgb(181,71,8)'} />
                    <IconChevronDown size={14} color="rgb(102,112,133)" />
                  </button>
                  {showComposerType && (
                    <Dropdown
                      items={composerModes}
                      value={composerType}
                      onPick={(k) => setComposerType(k as SendableType)}
                      onClose={() => setShowComposerType(false)}
                      up
                    />
                  )}
                </div>
                <input
                  value={draft}
                  onChange={(ev) => setDraft(ev.target.value)}
                  onKeyDown={(ev) => {
                    // Enter sends, Shift+Enter does not. This is a single-line input
                    // measured at 40px, so Shift+Enter cannot insert a newline
                    // either -- it is reserved rather than implemented, so that
                    // teaching this composer multi-line later does not change what
                    // a key people are already pressing does.
                    if (ev.key === 'Enter' && !ev.shiftKey) {
                      ev.preventDefault()
                      void onSend()
                    }
                  }}
                  placeholder={composerType === 'SMS'
                    ? 'Type a message'
                    : 'Type an internal note — the customer never sees it'}
                  className="min-w-0 flex-1 outline-none"
                  style={{ fontSize: 14, height: 38 }}
                />
                <button
                  onClick={() => void onSend()}
                  disabled={Boolean(blocked) || sending || !draft.trim()}
                  title={blocked ?? (composerType === 'SMS'
                    ? 'Send this text message'
                    : 'Save this internal note')}
                  className="mr-1 flex items-center gap-1"
                  style={{
                    height: 30, padding: '0 10px', borderRadius: 4,
                    color: '#fff', backgroundColor: 'rgb(21,112,239)',
                    opacity: (blocked || sending || !draft.trim()) ? 0.5 : 1,
                    cursor: (blocked || sending || !draft.trim())
                      ? 'not-allowed' : 'pointer',
                  }}
                >
                  <IconChat size={14} color="#fff" />
                  <span style={{ fontSize: 13 }}>{sending ? 'Sending…' : 'Send'}</span>
                </button>
              </div>
            </div>
          </div>

          {current && (
            <ContactDetailsPanel
              contactId={current.contact_id}
              user={user}
              // Deleting the contact deletes its conversations, so the thread this
              // pane is showing goes with it. Without this, `selected` still holds
              // the dead id and the thread pane sits empty after the list refetches.
              onDeleted={() => setSelected(null)}
            />
          )}
        </div>
      </div>
    </div>
  )
}
