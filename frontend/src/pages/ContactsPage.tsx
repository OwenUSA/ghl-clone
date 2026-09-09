import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import {
  IconDownload, IconFilter, IconList, IconPlus, IconSettings, IconSort,
} from '../components/Icon'
import { useState } from 'react'
import { ContactDetailsPanel } from '../components/ContactDetailsPanel'
import { createContact, listContacts } from '../lib/api'
import type { Me } from '../lib/auth'
import { IconChevronDown } from '../components/Icon'

/**
 * Measured from captures/contacts/*__1440x900__top__*.json:
 *   tab row      "Contacts" 18px/500 rgb(31,41,55) at y=49.4
 *                "Smart Lists" 14px/500 rgb(56,160,219) (active)
 *   page title   "Contacts" 20px/600 rgb(16,24,40) at x=239 y=105
 *   count pill   "268 Contacts" 14px/500 rgb(0,78,235)
 *   column pitch 162px  (291.8 / 453.8 / 615.8 / 777.8 / 939.8)
 *   header row   y=260.2, sticky - stays put while the pane scrolls
 *   page size    20, "Page 1 of 14", Prev/Next
 */
const TABS = ['Smart Lists', 'Bulk Actions', 'Custom Fields', 'Tasks', 'Companies']
// Measured column set, in GHL's order. Tags and Last activity were missing from
// the first build and found by capture/audit_report.py.
const COLUMNS = [
  { key: 'name', label: 'Contact name' },
  { key: 'phone', label: 'Phone' },
  { key: 'email', label: 'Email' },
  { key: 'business_name', label: 'Business name' },
  { key: 'created_at', label: 'Created (EDT)' },
  { key: 'tags', label: 'Tags' },
  { key: 'last_activity', label: 'Last activity (EDT)' },
] as const

const SORT_OPTIONS = [
  { key: 'created_at', label: 'Created (EDT)' },
  { key: 'name', label: 'Contact name' },
  { key: 'email', label: 'Email' },
  { key: 'phone', label: 'Phone' },
  { key: 'business_name', label: 'Business name' },
]

const AVATAR_HUES = [
  'rgb(249,219,175)', 'rgb(185,230,254)', 'rgb(153,246,224)',
  'rgb(217,214,254)', 'rgb(252,231,246)', 'rgb(199,215,254)',
]

/** Windowed page list with ellipses, e.g. 1 … 6 7 8 … 14. */
function pageNumbers(cur: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const out: (number | '…')[] = [1]
  const from = Math.max(2, cur - 1)
  const to = Math.min(total - 1, cur + 1)
  if (from > 2) out.push('…')
  for (let i = from; i <= to; i++) out.push(i)
  if (to < total - 1) out.push('…')
  out.push(total)
  return out
}

function initials(name: string) {
  const parts = name.split(' ').filter(Boolean)
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase()
}

export function ContactsPage({ user }: { user: Me }) {
  // `POST /api/contacts` is auth.STAFF, so a TECH's create is refused. Mirror that
  // here rather than let them fill six fields to find out on submit.
  const canCreate = user.role !== 'TECH'
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [q, setQ] = useState('')
  // Clicking a row opens the same measured Contact Details panel used by
  // Conversations, rather than a second divergent implementation.
  const [openContact, setOpenContact] = useState<number | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const qc = useQueryClient()

  const [sort, setSort] = useState('created_at')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [showSort, setShowSort] = useState(false)
  const [showFilters, setShowFilters] = useState(false)
  const [showFields, setShowFields] = useState(false)
  const [hidden, setHidden] = useState<string[]>([])

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['contacts', page, pageSize, q, sort, order],
    queryFn: () => listContacts({ page, page_size: pageSize, q, sort, order }),
    placeholderData: keepPreviousData,
  })

  const columns = COLUMNS.filter((c) => !hidden.includes(c.key))

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col" style={{ backgroundColor: 'rgb(249,250,251)' }}>
      {/* tab row */}
      <div
        className="flex shrink-0 items-center gap-6 bg-white px-4"
        style={{ height: 90, borderBottom: '1px solid rgb(234,236,240)' }}
      >
        <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)' }}>
          Contacts
        </div>
        {TABS.map((t, i) => (
          <div
            key={t}
            style={{
              fontSize: 14,
              fontWeight: 500,
              lineHeight: '25.6px',
              color: i === 0 ? 'rgb(56,160,219)' : 'rgb(102,112,133)',
            }}
          >
            {t}
          </div>
        ))}
      </div>

      {/* title + count */}
      <div className="flex shrink-0 items-center gap-4 px-4" style={{ height: 60 }}>
        <div style={{ fontSize: 20, fontWeight: 600, color: 'rgb(16,24,40)', lineHeight: '30px' }}>
          Contacts
        </div>
        <div
          className="flex items-center"
          style={{
            fontSize: 14,
            fontWeight: 500,
            color: 'rgb(0,78,235)',
            backgroundColor: 'rgb(239,244,255)',
            borderRadius: 16,
            padding: '4px 12px',
          }}
        >
          {data ? `${data.total} ${data.total === 1 ? 'Contact' : 'Contacts'}` : ' '}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <input
            value={q}
            onChange={(e) => {
              setPage(1)
              setQ(e.target.value)
            }}
            placeholder="Search"
            className="outline-none"
            style={{
              height: 36,
              width: 240,
              borderRadius: 6,
              border: '1px solid rgb(234,236,240)',
              padding: '8px 12px',
              fontSize: 14,
            }}
          />
          {/* Measured buttons. Import is out of v1 scope; Add Contact is real. */}
          <button
            disabled
            title="Contact import is not implemented in v1"
            style={{
              height: 36, padding: '0 10px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: 'rgb(0,78,235)', opacity: 0.5,
              cursor: 'not-allowed', display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <IconDownload size={16} color="rgb(0,78,235)" />
            Import
          </button>
          <button
            onClick={() => setShowAdd(true)}
            disabled={!canCreate}
            title={canCreate ? undefined : 'Your role cannot create contacts'}
            style={{
              height: 36, padding: '0 12px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              display: 'flex', alignItems: 'center', gap: 6,
              ...(canCreate ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
            }}
          >
            <IconPlus size={16} color="#fff" />
            Add Contact
          </button>
        </div>
      </div>

      {/* smart-list row — measured: "All" tab + "Add Smart List" */}
      <div className="flex shrink-0 items-center gap-6 px-4" style={{ height: 44 }}>
        <div className="flex items-center gap-2" style={{ paddingBottom: 6 }}>
          <IconList size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            All
          </span>
        </div>
        {/* GHL renders the "+" as its own element beside the label — measured */}
        <div
          className="flex items-center gap-1"
          title="Saved smart lists are not implemented in v1"
          style={{ cursor: 'not-allowed' }}
        >
          <IconPlus size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            Add Smart List
          </span>
        </div>
      </div>

      {/* toolbar — measured: Filters | Sort | ... | Manage fields */}
      <div className="relative flex shrink-0 items-center gap-2 px-4 pb-3">
        <button
          onClick={() => setShowFilters((s) => !s)}
          style={{
            height: 34, padding: '0 8px', borderRadius: 6, fontSize: 14,
            fontWeight: 500, color: 'rgb(52,64,84)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <IconFilter size={14} color="rgb(52,64,84)" />
          Filters
        </button>
        <button
          onClick={() => setShowSort((s) => !s)}
          style={{
            height: 34, padding: '0 8px', borderRadius: 6, fontSize: 14,
            fontWeight: 500, color: 'rgb(52,64,84)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <IconSort size={14} color="rgb(52,64,84)" />
          Sort
        </button>
        <button
          onClick={() => setShowFields((s) => !s)}
          className="ml-auto"
          style={{
            fontSize: 14, fontWeight: 600, color: 'rgb(71,84,103)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <IconSettings size={20} color="rgb(71,84,103)" />
          Manage fields
        </button>

        {showSort && (
          <div
            role="menu"
            className="absolute z-20 bg-white"
            style={{
              top: 40, left: 90, minWidth: 220, borderRadius: 8,
              border: '1px solid rgb(234,236,240)', padding: 4,
              boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)',
            }}
          >
            {SORT_OPTIONS.map((s) => (
              <button
                key={s.key}
                onClick={() => { setSort(s.key); setPage(1); setShowSort(false) }}
                className="block w-full text-left"
                style={{
                  padding: '8px 12px', borderRadius: 6, fontSize: 14,
                  color: 'rgb(52,64,84)',
                  backgroundColor: sort === s.key ? 'rgb(239,244,255)' : 'transparent',
                }}
              >
                {s.label}
              </button>
            ))}
            <button
              onClick={() => setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))}
              className="block w-full text-left"
              style={{ padding: '8px 12px', fontSize: 13, color: 'rgb(0,78,235)' }}
            >
              Direction: {order === 'asc' ? 'Ascending' : 'Descending'}
            </button>
          </div>
        )}

        {showFilters && (
          <div
            role="menu"
            className="absolute z-20 bg-white"
            style={{
              top: 40, left: 16, width: 320, borderRadius: 8,
              border: '1px solid rgb(234,236,240)', padding: 12,
              boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)',
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
              Filters
            </div>
            <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 8 }}>
              GHL's filter builder (Filter Type / Is / Value, AND/OR) is not
              implemented in v1. Search covers name, phone, email and business name.
            </div>
            <div className="mt-3 flex justify-end">
              <button onClick={() => setShowFilters(false)}
                style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 14, border: '1px solid rgb(234,236,240)' }}>
                Close
              </button>
            </div>
          </div>
        )}

        {showFields && (
          <div
            role="menu"
            className="absolute right-4 z-20 bg-white"
            style={{
              top: 40, width: 260, borderRadius: 8,
              border: '1px solid rgb(234,236,240)', padding: 12,
              boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)',
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)', marginBottom: 8 }}>
              Manage fields
            </div>
            {COLUMNS.map((c) => (
              <label key={c.key} className="flex items-center gap-2" style={{ fontSize: 14, padding: '4px 0' }}>
                <input
                  type="checkbox"
                  checked={!hidden.includes(c.key)}
                  onChange={(e) =>
                    setHidden((h) =>
                      e.target.checked ? h.filter((x) => x !== c.key) : [...h, c.key])
                  }
                />
                {c.label}
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="mx-4 mb-4 flex min-h-0 flex-1 gap-3">
      {/* table pane - THIS is what scrolls, not the document (measured) */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white"
        style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)' }}>
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse" style={{ minWidth: 1000 }}>
            <thead>
              <tr>
                <th style={{ width: 44 }} className="sticky top-0 z-10 bg-white">
                  <input type="checkbox" aria-label="Select all" />
                </th>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    className="sticky top-0 z-10 bg-white text-left"
                    style={{
                      width: 162,
                      height: 44,
                      padding: '0 12px',
                      // Measured, not assumed: GHL's column headers are
                      // 13px / 700 / rgb(71,84,103), not the 14px/500 used
                      // elsewhere in the app.
                      fontSize: 13,
                      fontWeight: 700,
                      color: 'rgb(71,84,103)',
                      borderBottom: '1px solid rgb(234,236,240)',
                    }}
                  >
                    <span className="flex items-center gap-1">
                      {c.label}
                      {/* measured: GHL renders a sort caret in the column header */}
                      <IconChevronDown
                        size={14}
                        color={sort === c.key ? 'rgb(0,78,235)' : 'rgb(152,162,179)'}
                      />
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={columns.length + 1} style={{ padding: 24, fontSize: 14 }}>
                    Loading…
                  </td>
                </tr>
              )}
              {isError && (
                <tr>
                  <td colSpan={columns.length + 1} style={{ padding: 24, fontSize: 14, color: 'rgb(217,45,32)' }}>
                    Could not load contacts: {String((error as Error).message)}
                  </td>
                </tr>
              )}
              {data?.items.length === 0 && (
                <tr>
                  <td colSpan={columns.length + 1} style={{ padding: 24, fontSize: 14 }}>
                    No contacts match “{q}”.
                  </td>
                </tr>
              )}
              {data?.items.map((c, i) => (
                <tr
                  key={c.id}
                  onClick={() => setOpenContact(c.id)}
                  className="cursor-pointer hover:bg-[rgb(249,250,251)]"
                  style={{
                    backgroundColor:
                      openContact === c.id ? 'rgb(239,244,255)' : undefined,
                  }}
                >
                  <td style={{ padding: '0 12px' }} onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" />
                  </td>
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={col.key === 'email' ? 'truncate' : undefined}
                      style={{
                        height: 52,
                        padding: '0 12px',
                        fontSize: 14,
                        maxWidth: col.key === 'email' ? 162 : undefined,
                        borderBottom: '1px solid rgb(242,244,247)',
                      }}
                    >
                      {col.key === 'name' ? (
                        <div className="flex items-center gap-2">
                          <div
                            className="flex shrink-0 items-center justify-center"
                            style={{
                              width: 28,
                              height: 28,
                              borderRadius: '50%',
                              backgroundColor: AVATAR_HUES[i % AVATAR_HUES.length],
                              fontSize: 12,
                              fontWeight: 500,
                              color: 'rgb(52,64,84)',
                            }}
                          >
                            {initials(c.name)}
                          </div>
                          <span className="truncate" style={{ color: 'rgb(52,64,84)' }}>
                            {c.name}
                          </span>
                        </div>
                      ) : col.key === 'tags' ? (
                        <div className="flex flex-wrap gap-1">
                          {(c.tags ?? []).map((t) => (
                            <span
                              key={t}
                              style={{
                                fontSize: 12,
                                fontWeight: 500,
                                color: 'rgb(71,84,103)',
                                backgroundColor: 'rgb(242,244,247)',
                                borderRadius: 6,
                                padding: '2px 6px',
                              }}
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      ) : col.key === 'created_at' ? (
                        new Date(c.created_at).toLocaleDateString('en-US')
                      ) : col.key === 'last_activity' ? (
                        c.last_activity
                          ? new Date(c.last_activity).toLocaleDateString('en-US')
                          : ''
                      ) : (
                        (c[col.key as 'phone' | 'email' | 'business_name'] ?? '')
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* pagination - measured: "Page 1 of 14", size select, Prev / Next */}
        <div
          className="flex shrink-0 items-center gap-3 px-4"
          style={{ height: 52, borderTop: '1px solid rgb(234,236,240)', fontSize: 14 }}
        >
          <span>
            Page {data?.page ?? 1} of {data?.pages ?? 1}
          </span>
          <select
            value={pageSize}
            onChange={(e) => {
              setPage(1)
              setPageSize(Number(e.target.value))
            }}
            style={{ height: 32, borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 8px' }}
          >
            {[20, 50, 100].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          {/* Measured: Prev/Next are 14px/500 rgb(71,84,103), and GHL renders
              NUMBERED page buttons (current page 14px/500 rgb(23,92,211)).
              Ours had no page numbers at all. */}
          <div className="ml-auto flex items-center gap-2">
            <button
              disabled={(data?.page ?? 1) <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              style={{
                height: 32, padding: '0 12px', borderRadius: 6,
                fontSize: 14, fontWeight: 500, color: 'rgb(71,84,103)',
                border: '1px solid rgb(234,236,240)',
                opacity: (data?.page ?? 1) <= 1 ? 0.5 : 1,
              }}
            >
              Prev
            </button>

            {pageNumbers(data?.page ?? 1, data?.pages ?? 1).map((n, i) =>
              n === '…' ? (
                <span key={`gap${i}`} style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
                  …
                </span>
              ) : (
                <button
                  key={n}
                  onClick={() => setPage(n as number)}
                  style={{
                    minWidth: 32, height: 32, borderRadius: 6, fontSize: 14,
                    fontWeight: 500,
                    color: n === (data?.page ?? 1) ? 'rgb(23,92,211)' : 'rgb(71,84,103)',
                    backgroundColor:
                      n === (data?.page ?? 1) ? 'rgb(239,244,255)' : 'transparent',
                  }}
                >
                  {n}
                </button>
              ),
            )}

            <button
              disabled={(data?.page ?? 1) >= (data?.pages ?? 1)}
              onClick={() => setPage((p) => p + 1)}
              style={{
                height: 32, padding: '0 12px', borderRadius: 6,
                fontSize: 14, fontWeight: 500, color: 'rgb(71,84,103)',
                border: '1px solid rgb(234,236,240)',
                opacity: (data?.page ?? 1) >= (data?.pages ?? 1) ? 0.5 : 1,
              }}
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {showAdd && <AddContactDialog onClose={() => setShowAdd(false)} onDone={() => {
        setShowAdd(false)
        qc.invalidateQueries({ queryKey: ['contacts'] })
      }} />}

      {openContact != null && (
        <ContactDetailsPanel
          contactId={openContact}
          onClose={() => setOpenContact(null)}
        />
      )}
      </div>
    </div>
  )
}

function AddContactDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState({
    first_name: '', last_name: '', phone: '', email: '', business_name: '', source: '',
  })
  const [error, setError] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => createContact(form),
    onSuccess: onDone,
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
      onClick={onClose}
    >
      <div onClick={(e) => e.stopPropagation()} className="bg-white"
        style={{ width: 460, borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 18, fontWeight: 600, color: 'rgb(16,24,40)' }}>Add Contact</div>
        {([
          ['first_name', 'First name'], ['last_name', 'Last name'],
          ['phone', 'Phone'], ['email', 'Email'],
          ['business_name', 'Business name'], ['source', 'Contact source'],
        ] as const).map(([k, label]) => (
          <div key={k} style={{ marginTop: 12 }}>
            <div style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>{label}</div>
            <input
              value={form[k]}
              onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
              style={{
                width: '100%', height: 36, marginTop: 4, fontSize: 14,
                borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
              }}
            />
          </div>
        ))}
        {error && <div style={{ fontSize: 13, color: 'rgb(217,45,32)', marginTop: 10 }}>{error}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, border: '1px solid rgb(234,236,240)' }}>
            Cancel
          </button>
          <button onClick={() => { setError(null); create.mutate() }} disabled={create.isPending}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)' }}>
            Create
          </button>
        </div>
      </div>
    </div>
  )
}
