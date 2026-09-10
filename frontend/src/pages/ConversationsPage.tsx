import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { Me } from '../lib/auth'
import { ContactDetailsPanel } from '../components/ContactDetailsPanel'
import {
  IconCalendar, IconChat, IconChevronDown, IconClock, IconEye, IconFilter,
  IconFunnel, IconInbox, IconMail, IconPhone, IconPlay, IconSearch, IconSort,
  IconStar, IconTrash, IconUser, IconUsers,
} from '../components/Icon'
import {
  PANE, listConversations, listEvents,
  type ConversationSummary, type ThreadEvent,
} from '../lib/api'
import type { Focus } from '../lib/focus'

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
  children, title, onClick, size = 24,
}: { children: React.ReactNode; title: string; onClick?: () => void; size?: number }) {
  return (
    <button
      title={title}
      aria-label={title}
      onClick={onClick}
      className="flex items-center justify-center"
      style={{ width: size, height: size, color: 'rgb(71,84,103)' }}
    >
      {children}
    </button>
  )
}

function Dropdown({
  items, value, onPick, onClose, right = false,
}: {
  items: { key: string; label: string; unimplemented?: boolean; blocked?: string }[]
  value: string
  onPick: (k: string) => void
  onClose: () => void
  right?: boolean
}) {
  return (
    <div
      role="menu"
      className="absolute z-30 mt-1 bg-white"
      style={{
        // `top` must be set explicitly. Without it the menu falls back to its static
        // position, and its `relative` parent is a flex row with `items-center`, so the
        // menu is vertically CENTRED on the 24px icon row and overflows upward off the
        // top of the window. 100% drops it below the row, where `mt-1` spaces it.
        top: '100%',
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
                <IconPhone size={14} color="rgb(18,183,106)" />
                Call completed
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
          {e.delivery_status === 'LOGGED_ONLY' && ' · not sent (stub transport)'}
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

  const convs = useQuery({
    queryKey: ['conversations', tab, sort],
    queryFn: () => listConversations(tab, sort),
  })
  const active = selected ?? convs.data?.[0]?.id ?? null
  const events = useQuery({
    queryKey: ['events', active, filter],
    queryFn: () => listEvents(active as number, filter),
    enabled: active != null,
  })
  const current: ConversationSummary | undefined = convs.data?.find((c) => c.id === active)

  const unreadTotal = (convs.data ?? []).reduce((s, c) => s + c.unread_count, 0)
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
                <IconBtn title="Call (stubbed in v1)"><IconPhone size={24} color="rgb(71,84,103)" /></IconBtn>
                <IconBtn title="Add to Favorites"><IconStar size={24} color="rgb(71,84,103)" /></IconBtn>
                <IconBtn title="Mark as read"><IconMail size={24} color="rgb(71,84,103)" /></IconBtn>
                <IconBtn title="Delete Conversation (not implemented in v1)">
                  <IconTrash size={24} color="rgb(71,84,103)" />
                </IconBtn>
                {showFilter && (
                  <Dropdown items={filters} value={filter} onPick={setFilter} right
                    onClose={() => setShowFilter(false)} />
                )}
              </div>
            </div>

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
                  {/* unread divider — measured 14px/500 rgb(41,112,255) */}
                  {(current?.unread_count ?? 0) > 0 && (
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
              <div className="flex items-center"
                style={{
                  height: 40, borderRadius: 4, backgroundColor: '#fff',
                  border: '1px solid rgb(234,236,240)',
                }}>
                <div className="flex shrink-0 items-center justify-center gap-1"
                  style={{ width: 54 }}>
                  <IconChat size={16} color="rgb(21,112,239)" />
                  <IconChevronDown size={14} color="rgb(102,112,133)" />
                </div>
                <input
                  value={draft}
                  onChange={(ev) => setDraft(ev.target.value)}
                  placeholder="Type a message"
                  className="min-w-0 flex-1 outline-none"
                  style={{ fontSize: 14, height: 38 }}
                />
                <button
                  disabled
                  title="SMS is UI-only in v1 — the transport is a logging no-op"
                  className="mr-1 flex items-center gap-1"
                  style={{
                    height: 30, padding: '0 10px', borderRadius: 4,
                    color: '#fff', backgroundColor: 'rgb(21,112,239)',
                    opacity: 0.5, cursor: 'not-allowed',
                  }}
                >
                  <IconChat size={14} color="#fff" />
                  <IconChevronDown size={12} color="#fff" />
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
