import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  confirmZuperSetupItem, getZuperStatus, listZuperConflicts, listZuperDeletes,
  putZuperSettings, restoreZuperDelete, runZuperSetupCheck,
} from '../lib/api'
import {
  countTable, COUNT_COLUMNS, crmTypeLabel, cutoverSentence, deleteStateLabel,
  directionLabel, fieldLabel, filterCheckLabel, loadSummary, pageCount, ruleLabel, setupChip,
  sideLabel, stamp, switchView, syncChip, valueText,
  type ZuperDelete, type ZuperRestoreResult, type ZuperSetupItem, type ZuperStatus,
} from '../lib/zuper'
import {
  BODY, BORDER, BUTTON, DANGER, DIVIDER, ErrorLine, FAINT, HEADING, INPUT, MUTED, PRIMARY,
  PRIMARY_BUTTON, TEXT, dead,
} from './opportunity/ui'
import { Chip, Confirm } from './ZuperMoneyPanel'

/**
 * Settings → Zuper (2026-09-16). ADMIN only: the section is not drawn for anyone else, and
 * every route it reads answers 403 regardless.
 *
 *   Connection        ZUPER_SYNC_ENABLED · key · base URL · webhook token · On / Paused / Off
 *   Sync with Zuper   the switch — or, while anything blocks it, the list of what does
 *   Workiz cutover    the date Workiz retires; the importer refuses to run from that day
 *   Setup check       PASS / FAIL / Confirm by hand, each by-hand item ticked by an admin
 *   Heartbeat · Records by type · Problems · Webhook inbox · Initial load
 *   Conflict log      every overwrite, with the rule that decided it
 *   Deleted by sync   every mirrored delete, with Restore
 *
 * OUR design (GoHighLevel has no Zuper page), in Settings → CompanyCam's cards and My Staff's
 * tables. A control that cannot work is not drawn: a blocked switch is a list of reasons.
 */

const PAGE_SIZE = 25

const card: React.CSSProperties = {
  border: '1px solid ' + DIVIDER, borderRadius: 8, padding: '12px 16px', marginTop: 12,
  backgroundColor: '#fff',
}
const head: React.CSSProperties = {
  fontSize: 12, fontWeight: 500, color: BODY, textAlign: 'left', padding: '0 12px', height: 36,
  backgroundColor: 'rgb(249,250,251)', borderBottom: '1px solid ' + DIVIDER, whiteSpace: 'nowrap',
}
const cell: React.CSSProperties = {
  padding: '10px 12px', borderBottom: '1px solid ' + DIVIDER, verticalAlign: 'top', fontSize: 13,
  color: TEXT,
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4" style={{ padding: '6px 0', fontSize: 14 }}>
      <div style={{ width: 220, flexShrink: 0, color: MUTED }}>{label}</div>
      <div className="min-w-0 flex-1" style={{ color: TEXT, overflowWrap: 'anywhere' }}>{children}</div>
    </div>
  )
}

function Table({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ overflowX: 'auto', marginTop: 8, border: '1px solid ' + DIVIDER, borderRadius: 4 }}>
      <table aria-label={label} style={{ width: '100%', borderCollapse: 'collapse' }}>{children}</table>
    </div>
  )
}

function Pager({ page, total, onPage }: { page: number; total: number; onPage: (n: number) => void }) {
  const pages = pageCount(total, PAGE_SIZE)
  if (total <= PAGE_SIZE) return null
  const pager: React.CSSProperties = { height: 32, padding: '0 12px', borderRadius: 4,
    border: '1px solid ' + DIVIDER, fontSize: 13 }
  return (
    <div className="flex items-center" style={{ padding: '12px 0' }}>
      <span style={{ fontSize: 14, color: TEXT }}>Page {page} of {pages}</span>
      <div className="ml-auto flex items-center gap-3">
        <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}
          style={{ ...pager, color: page <= 1 ? 'rgb(152,162,179)' : TEXT }}>Previous</button>
        <span aria-current="page" style={{ minWidth: 24, height: 26, lineHeight: '24px',
          textAlign: 'center', borderRadius: 3, fontSize: 13, border: '1px solid ' + PRIMARY,
          color: PRIMARY }}>{page}</span>
        <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}
          style={{ ...pager, color: page >= pages ? 'rgb(152,162,179)' : TEXT }}>Next</button>
      </div>
    </div>
  )
}

export function ZuperSettings() {
  const status = useQuery({ queryKey: ['zuper-status'], queryFn: getZuperStatus })
  const s = status.data

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 32 }}>
      <section style={{ maxWidth: 960 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: TEXT }}>Zuper</div>
        <div style={{ fontSize: 14, color: FAINT, marginTop: 4, lineHeight: 1.5 }}>
          Contacts, opportunities, visits, notes and tasks stay in step with Zuper. Quotes,
          invoices and payments belong to Zuper and show here read-only.
        </div>
        {status.isError && <ErrorLine error={(status.error as Error).message} />}
        {status.isLoading && <div style={{ ...card, fontSize: 14, color: FAINT }}>Loading…</div>}
        {s && (
          <>
            <ConnectionCard s={s} />
            <SwitchCard s={s} />
            <CutoverCard s={s} />
            <SetupCard s={s} />
            <HeartbeatCard s={s} />
            <CountsCard s={s} />
            <ProblemsCard s={s} />
            <InboxCard s={s} />
            {s.load_report && <LoadReportCard s={s} />}
          </>
        )}
      </section>
      {s && <ConflictLog />}
      {s && <DeleteLog />}
    </div>
  )
}

function ConnectionCard({ s }: { s: ZuperStatus }) {
  const chip = syncChip(s.sync)
  return (
    <div style={card} data-card="connection">
      <div className="flex items-center gap-3">
        <div style={HEADING}>Connection</div>
        <span data-testid="sync-chip"><Chip {...chip} /></span>
      </div>
      <Row label="ZUPER_SYNC_ENABLED">{s.config.env_enabled ? 'true' : 'Not true — the sync is off on this deployment'}</Row>
      <Row label="API key">{s.config.key_set ? 'Set' : 'Not set'}</Row>
      <Row label="Base URL">{s.config.base_url}</Row>
      <Row label="Webhook token">{s.config.webhook_token_set ? 'Set' : 'Not set — no delivery is accepted'}</Row>
      <Row label="CRM link base">{s.config.crm_base_url}</Row>
      {s.enabled_at && <Row label="First switched on">{stamp(s.enabled_at)}</Row>}
    </div>
  )
}

function SwitchCard({ s }: { s: ZuperStatus }) {
  const qc = useQueryClient()
  const [asking, setAsking] = useState<'on' | 'pause' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const change = useMutation({
    mutationFn: (enabled: boolean) => putZuperSettings({ enabled }),
    onSuccess: (data) => { qc.setQueryData(['zuper-status'], data); setAsking(null) },
    onError: (e: Error) => { setAsking(null); setError(e.message) },
  })
  const view = switchView(s)
  return (
    <div style={card} data-card="switch">
      <div style={HEADING}>Sync with Zuper</div>
      {view === 'blocked' ? (
        <div data-testid="blockers" style={{ marginTop: 8 }}>
          <div style={{ fontSize: 14, color: TEXT }}>The sync can be switched on when:</div>
          <ul style={{ margin: '6px 0 0', paddingLeft: 20, listStyle: 'disc' }}>
            {s.blockers.map((b) => (
              <li key={b} style={{ fontSize: 14, color: BODY, padding: '2px 0' }}>{b}</li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="flex items-center gap-3" style={{ marginTop: 10 }}>
          <div className="flex-1">
            <div style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>
              {view === 'pause' ? 'The sync is switched on' : 'The sync is switched off'}
            </div>
            <div style={{ fontSize: 13, color: MUTED, marginTop: 2 }}>
              {view === 'pause'
                ? 'Switching it off pauses every push, pull, sweep and webhook at once.'
                : 'Every check has passed. Switching it on starts writing to Zuper.'}
            </div>
          </div>
          <button type="button" role="switch" aria-checked={s.enabled} aria-label="Sync with Zuper"
            onClick={() => { setError(null); setAsking(s.enabled ? 'pause' : 'on') }}
            style={{ width: 36, height: 20, borderRadius: 10, position: 'relative', flexShrink: 0,
              backgroundColor: s.enabled ? PRIMARY : 'rgb(234,236,240)',
              transition: 'background-color 120ms' }}>
            <span style={{ position: 'absolute', top: 2, width: 16, height: 16, borderRadius: 8,
              left: s.enabled ? 18 : 2, backgroundColor: '#fff',
              boxShadow: '0 1px 3px rgba(16,24,40,0.1)', transition: 'left 120ms' }} />
          </button>
        </div>
      )}
      <ErrorLine error={error} />
      {asking === 'on' && (
        <Confirm title="Switch the Zuper sync on?" action="Switch on" busy={change.isPending}
          onCancel={() => setAsking(null)} onConfirm={() => change.mutate(true)}>
          The CRM starts writing to Zuper: new and changed contacts, opportunities, visits, notes
          and tasks are pushed, Zuper's changes are pulled in, and deletes are mirrored both ways
          (each one restorable here). No customer is messaged by the sync.
        </Confirm>
      )}
      {asking === 'pause' && (
        <Confirm title="Pause the Zuper sync?" action="Pause sync" danger busy={change.isPending}
          onCancel={() => setAsking(null)} onConfirm={() => change.mutate(false)}>
          Nothing is sent to or read from Zuper until it is switched on again. Changes made
          meanwhile are found by the first sweep after that.
        </Confirm>
      )}
    </div>
  )
}

function CutoverCard({ s }: { s: ZuperStatus }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState(s.workiz_cutover_date ?? '')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { setDraft(s.workiz_cutover_date ?? '') }, [s.workiz_cutover_date])
  const save = useMutation({
    mutationFn: (value: string | null) => putZuperSettings({ workiz_cutover_date: value }),
    onSuccess: (data) => qc.setQueryData(['zuper-status'], data),
    onError: (e: Error) => setError(e.message),
  })
  const changed = draft !== (s.workiz_cutover_date ?? '')
  const canSave = !!draft && changed && !save.isPending
  return (
    <div style={card} data-card="cutover">
      <div style={HEADING}>Workiz cutover date</div>
      <div data-testid="cutover-sentence" style={{ fontSize: 14, marginTop: 6,
        color: s.workiz_cutover_date && !s.before_cutover ? DANGER : TEXT }}>
        {cutoverSentence(s.workiz_cutover_date, s.before_cutover)}
      </div>
      <div style={{ fontSize: 13, color: MUTED, marginTop: 2 }}>
        Until this day Workiz wins a Workiz job's title, stage, address and schedule. From this
        day the Workiz importer refuses to run.
      </div>
      <div className="flex items-center gap-2" style={{ marginTop: 10 }}>
        <input type="date" aria-label="Workiz cutover date" value={draft}
          onChange={(e) => setDraft(e.target.value)}
          style={{ ...INPUT, marginTop: 0, width: 180 }} />
        <button type="button" disabled={!canSave} onClick={() => { setError(null); save.mutate(draft) }}
          style={{ ...PRIMARY_BUTTON, height: 36, ...dead(canSave) }}>
          Save
        </button>
        {s.workiz_cutover_date && (
          <button type="button" disabled={save.isPending}
            onClick={() => { setError(null); save.mutate(null) }}
            style={{ ...BUTTON, height: 36, ...dead(!save.isPending) }}>
            Clear
          </button>
        )}
      </div>
      <ErrorLine error={error} />
    </div>
  )
}

function SetupCard({ s }: { s: ZuperStatus }) {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const check = useMutation({
    mutationFn: runZuperSetupCheck,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['zuper-status'] }),
    onError: (e: Error) => setError(e.message),
  })
  const canRun = s.config.env_enabled && s.config.key_set
  const why = !s.config.env_enabled
    ? 'The setup check cannot run here: ZUPER_SYNC_ENABLED is not true on this deployment.'
    : 'The setup check cannot run here: no Zuper API key is configured.'
  const results = s.setup.results
  return (
    <div style={card} data-card="setup">
      <div className="flex items-center gap-3">
        <div style={HEADING} className="flex-1">Setup check</div>
        {canRun && (
          <button type="button" disabled={check.isPending}
            onClick={() => { setError(null); check.mutate() }}
            style={{ ...PRIMARY_BUTTON, height: 36, ...dead(!check.isPending) }}>
            {check.isPending ? 'Checking…' : 'Run setup check'}
          </button>
        )}
      </div>
      {!canRun && <div style={{ fontSize: 14, color: MUTED, marginTop: 6 }}>{why}</div>}
      <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>
        {s.setup.checked_at
          ? `Last checked ${stamp(s.setup.checked_at)} — ${results.filter((r) => r.state === 'pass').length} pass, `
            + `${results.filter((r) => r.state === 'fail').length} fail, `
            + `${results.filter((r) => r.state === 'confirm_by_hand').length} to confirm by hand.`
          : 'Not run yet.'}
      </div>
      <ErrorLine error={error} />
      {results.length > 0 && (
        <div role="list" aria-label="Setup check results" style={{ marginTop: 8 }}>
          {results.map((r, i) => <SetupRow key={r.key + i} item={r} />)}
        </div>
      )}
    </div>
  )
}

function SetupRow({ item }: { item: ZuperSetupItem }) {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const confirm = useMutation({
    mutationFn: (confirmed: boolean) => confirmZuperSetupItem(item.key, confirmed),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['zuper-status'] }),
    onError: (e: Error) => setError(e.message),
  })
  const chip = setupChip(item.state)
  return (
    <div role="listitem" data-setup-item={item.key} data-state={item.state}
      style={{ borderTop: '1px solid ' + DIVIDER, padding: '10px 0' }}>
      <div className="flex items-start gap-3">
        <div style={{ width: 128, flexShrink: 0 }}><Chip {...chip} /></div>
        <div className="min-w-0 flex-1">
          <div style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>{item.title}</div>
          {item.sentences.map((line, i) => (
            <div key={i} style={{ fontSize: 13, color: item.state === 'fail' ? DANGER : MUTED,
              marginTop: 2, overflowWrap: 'anywhere' }}>{line}</div>
          ))}
          {item.state === 'confirm_by_hand' && (
            <label className="flex items-center gap-2" style={{ marginTop: 6, fontSize: 14, color: BODY }}>
              <input type="checkbox" checked={!!item.confirmed} disabled={confirm.isPending}
                aria-label={`Confirmed by hand: ${item.title}`}
                onChange={(e) => { setError(null); confirm.mutate(e.target.checked) }}
                style={{ width: 16, height: 16, accentColor: PRIMARY }} />
              Confirmed by hand
              {item.confirmed && (
                <span style={{ fontSize: 13, color: MUTED }}>
                  by {item.confirmed.by} at {stamp(item.confirmed.at)}
                </span>
              )}
            </label>
          )}
          <ErrorLine error={error} />
        </div>
      </div>
    </div>
  )
}

function HeartbeatCard({ s }: { s: ZuperStatus }) {
  const beat = s.heartbeat
  const queue = Object.entries(s.queue.by_status)
  return (
    <div style={card} data-card="heartbeat">
      <div style={HEADING}>Heartbeat</div>
      {!beat && <Row label="Sync activity">Nothing has run yet.</Row>}
      {beat && (
        <>
          <Row label="Last sweep started">{stamp(beat.last_sweep_started_at)}</Row>
          <Row label="Last sweep succeeded">{stamp(beat.last_sweep_success_at)}</Row>
          <Row label="Sweep cursor">{stamp(beat.sweep_cursor)}</Row>
          {beat.filter_checks && Object.keys(beat.filter_checks).length > 0 && (
            <Row label="Updated-since filter">
              {Object.entries(beat.filter_checks).map(([module, narrows]) => (
                <div key={module}>{module}: {filterCheckLabel(!!narrows)}</div>
              ))}
            </Row>
          )}
          <Row label="Last push to Zuper">{stamp(beat.last_push_at)}</Row>
          <Row label="Last pull from Zuper">{stamp(beat.last_pull_at)}</Row>
          <Row label="Last webhook">{stamp(beat.last_webhook_at)}</Row>
          <Row label="Last error">
            {beat.last_error
              ? <span style={{ color: DANGER }}>{beat.last_error}
                  <span style={{ color: MUTED }}> — {stamp(beat.last_error_at)}</span></span>
              : 'None'}
          </Row>
          {beat.last_counts && Object.keys(beat.last_counts).length > 0 && (
            <Row label="Last counts">
              {Object.entries(beat.last_counts).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ')}
            </Row>
          )}
        </>
      )}
      <Row label="Queued sync jobs">
        {queue.length ? queue.map(([k, v]) => `${k} ${v}`).join(' · ') : 'None'}
      </Row>
    </div>
  )
}

function CountsCard({ s }: { s: ZuperStatus }) {
  return (
    <div style={card} data-card="counts">
      <div style={HEADING}>Records by type</div>
      <Table label="Linked records by type">
        <thead>
          <tr>
            <th style={head}>Type</th>
            {COUNT_COLUMNS.map(([k, label]) => <th key={k} style={{ ...head, textAlign: 'right' }}>{label}</th>)}
          </tr>
        </thead>
        <tbody>
          {countTable(s.counts).map((row) => (
            <tr key={row.key} data-count-row={row.key}>
              <td style={cell}>{row.label}</td>
              {row.cells.map((n, i) => (
                <td key={i} style={{ ...cell, textAlign: 'right', color: n ? TEXT : FAINT }}>{n}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

function ProblemsCard({ s }: { s: ZuperStatus }) {
  const inboxFailures = s.inbox.recent.filter((d) => d.status === 'failed')
  const beat = s.heartbeat
  const none = !beat?.last_error && s.queue.recent_failures.length === 0 && inboxFailures.length === 0
  return (
    <div style={card} data-card="problems">
      <div style={HEADING}>Problems</div>
      {none && <div style={{ fontSize: 14, color: MUTED, marginTop: 6 }}>No errors.</div>}
      {beat?.last_error && (
        <div role="alert" style={{ fontSize: 14, color: DANGER, marginTop: 6 }}>
          {beat.last_error} <span style={{ color: MUTED }}>({stamp(beat.last_error_at)})</span>
        </div>
      )}
      {s.queue.recent_failures.length > 0 && (
        <Table label="Failed sync jobs">
          <thead><tr>
            <th style={head}>When</th><th style={head}>Job</th><th style={head}>What went wrong</th>
          </tr></thead>
          <tbody>
            {s.queue.recent_failures.map((f) => (
              <tr key={f.id}>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>{stamp(f.at)}</td>
                <td style={cell}>{f.type === 'zuper_delete' ? 'Mirror a delete' : 'Push a change'}</td>
                <td style={{ ...cell, color: DANGER }}>{f.error ?? 'No reason was recorded.'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {inboxFailures.length > 0 && (
        <Table label="Failed webhook deliveries">
          <thead><tr>
            <th style={head}>Received</th><th style={head}>Delivery</th><th style={head}>What went wrong</th>
          </tr></thead>
          <tbody>
            {inboxFailures.map((d) => (
              <tr key={d.id}>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>{stamp(d.received_at)}</td>
                <td style={cell}>{[d.event, d.module].filter(Boolean).join(' · ') || '—'}</td>
                <td style={{ ...cell, color: DANGER }}>{d.error ?? 'No reason was recorded.'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}

function InboxCard({ s }: { s: ZuperStatus }) {
  const counts = Object.entries(s.inbox.by_status)
  return (
    <div style={card} data-card="inbox">
      <div style={HEADING}>Webhook inbox</div>
      <Row label="Deliveries">
        {counts.length ? counts.map(([k, v]) => `${k} ${v}`).join(' · ') : 'None received yet'}
      </Row>
      {s.inbox.recent.length > 0 && (
        <Table label="Recent webhook deliveries">
          <thead><tr>
            {['Received', 'Event', 'Module', 'Record', 'Status', 'Outcome', 'Signature'].map((h) => (
              <th key={h} style={head}>{h}</th>
            ))}
          </tr></thead>
          <tbody>
            {s.inbox.recent.map((d) => (
              <tr key={d.id}>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>{stamp(d.received_at)}</td>
                <td style={cell}>{d.event ?? '—'}</td>
                <td style={cell}>{d.module ?? '—'}</td>
                <td style={{ ...cell, fontFamily: 'monospace', fontSize: 12 }}>{d.record_uid ?? '—'}</td>
                <td style={cell}>{d.status}</td>
                <td style={{ ...cell, color: d.error ? DANGER : TEXT }}>{d.error ?? d.outcome ?? '—'}</td>
                <td style={cell}>{d.signature}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}

function LoadReportCard({ s }: { s: ZuperStatus }) {
  const r = loadSummary(s.load_report)
  if (!r) return null
  return (
    <div style={card} data-card="load-report">
      <div style={HEADING}>Initial load</div>
      <Row label="Mode">{r.mode}</Row>
      <Row label="Started">{stamp(r.startedAt)}</Row>
      {r.refused && <Row label="Refused"><span style={{ color: DANGER }}>{r.refused}</span></Row>}
      {r.stopped && <Row label="Stopped"><span style={{ color: DANGER }}>{r.stopped}</span></Row>}
      {r.phases.map((p) => (
        <Row key={p.kind} label={p.kind}>
          {p.counts || '—'}
          {p.failedIds.length > 0 && (
            <div style={{ color: DANGER }}>Failed ids: {p.failedIds.join(', ')}</div>
          )}
        </Row>
      ))}
      {r.mismatchTotal != null && (
        <Row label="Verification">
          {r.mismatchTotal === 0 ? 'No mismatches' : `${r.mismatchTotal} mismatch(es)`}
          {r.mismatches.map((m) => (
            <div key={m.kind + m.what} style={{ color: MUTED, fontSize: 13 }}>
              {m.kind} — {m.what}: {m.ids.join(', ')}
            </div>
          ))}
        </Row>
      )}
    </div>
  )
}

function ConflictLog() {
  const [page, setPage] = useState(1)
  const list = useQuery({
    queryKey: ['zuper-conflicts', page],
    queryFn: () => listZuperConflicts(page, PAGE_SIZE),
  })
  const items = list.data?.items ?? []
  return (
    <section style={{ maxWidth: 960, marginTop: 32 }} data-section="conflicts">
      <div style={{ fontSize: 16, fontWeight: 600, color: TEXT }}>
        Conflict log {list.data ? `(${list.data.total})` : ''}
      </div>
      <div style={{ fontSize: 14, color: FAINT, marginTop: 4 }}>
        Every value one side had changed that the sync then overwrote, and the rule that decided.
      </div>
      {list.isError && <ErrorLine error={(list.error as Error).message} />}
      {list.data && items.length === 0 && (
        <div style={{ ...card, fontSize: 14, color: FAINT }}>No conflicts yet.</div>
      )}
      {items.length > 0 && (
        <Table label="Conflict log">
          <thead><tr>
            {['When', 'Record', 'Field', 'Rule', 'Written to', 'Before → After'].map((h) => (
              <th key={h} style={head}>{h}</th>
            ))}
          </tr></thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.id} data-conflict={c.id}>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>{stamp(c.occurred_at)}</td>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                  {crmTypeLabel(c.crm_type)}{c.crm_id != null ? ` #${c.crm_id}` : ''}
                  {c.zuper_uid && (
                    <div style={{ fontSize: 12, color: FAINT, fontFamily: 'monospace' }}>{c.zuper_uid}</div>
                  )}
                </td>
                <td style={cell}>{fieldLabel(c.field)}</td>
                <td style={cell}>{ruleLabel(c.rule)}</td>
                <td style={cell}>{sideLabel(c.written_to)}</td>
                <td style={{ ...cell, overflowWrap: 'anywhere', minWidth: 200 }}>
                  <span style={{ color: MUTED }}>{valueText(c.before, c.field)}</span>
                  {' → '}
                  <span>{valueText(c.after, c.field)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {list.data && <Pager page={page} total={list.data.total} onPage={setPage} />}
    </section>
  )
}

function DeleteLog() {
  const qc = useQueryClient()
  const [page, setPage] = useState(1)
  const [asking, setAsking] = useState<ZuperDelete | null>(null)
  const [result, setResult] = useState<ZuperRestoreResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const list = useQuery({
    queryKey: ['zuper-deletes', page],
    queryFn: () => listZuperDeletes(page, PAGE_SIZE),
  })
  const restore = useMutation({
    mutationFn: (id: number) => restoreZuperDelete(id),
    onSuccess: (data) => {
      setResult(data)
      setAsking(null)
      qc.invalidateQueries({ queryKey: ['zuper-deletes'] })
      qc.invalidateQueries({ queryKey: ['zuper-status'] })
      qc.invalidateQueries({ queryKey: ['opportunities'] })
      qc.invalidateQueries({ queryKey: ['contacts'] })
    },
    onError: (e: Error) => { setAsking(null); setError(e.message) },
  })
  const items = list.data?.items ?? []
  return (
    <section style={{ maxWidth: 960, marginTop: 32, paddingBottom: 32 }} data-section="deletes">
      <div style={{ fontSize: 16, fontWeight: 600, color: TEXT }}>
        Deleted by sync {list.data ? `(${list.data.total})` : ''}
      </div>
      <div style={{ fontSize: 14, color: FAINT, marginTop: 4 }}>
        A full copy of each record is kept before its delete is mirrored. Restore brings it back
        on both sides, with what was deleted alongside it.
      </div>
      {list.isError && <ErrorLine error={(list.error as Error).message} />}
      <ErrorLine error={error} />
      {result && (
        <div role="status" data-testid="restore-result" className="flex items-start gap-3"
          style={{ marginTop: 12, fontSize: 13, borderRadius: 8, padding: '8px 12px',
            color: result.results.some((r) => r.state !== 'restored') ? 'rgb(180,35,24)' : 'rgb(2,122,72)',
            backgroundColor: result.results.some((r) => r.state !== 'restored') ? 'rgb(254,243,242)' : 'rgb(236,253,243)',
            border: '1px solid ' + BORDER }}>
          <div className="flex-1">
            {result.results.map((r) => <div key={r.id}>{r.detail ?? deleteStateLabel(r.state)}</div>)}
          </div>
          <button type="button" aria-label="Dismiss" onClick={() => setResult(null)}>
            <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth={2} aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>
      )}
      {list.data && items.length === 0 && (
        <div style={{ ...card, fontSize: 14, color: FAINT }}>Nothing has been deleted by the sync.</div>
      )}
      {items.length > 0 && (
        <Table label="Deleted by sync">
          <thead><tr>
            {['When', 'What happened', 'Record', 'State', ''].map((h, i) => (
              <th key={i} style={head}>{h}</th>
            ))}
          </tr></thead>
          <tbody>
            {items.map((d) => (
              <tr key={d.id} data-delete={d.id}>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>{stamp(d.occurred_at)}</td>
                <td style={cell}>{directionLabel(d.direction)}</td>
                <td style={cell}>
                  {crmTypeLabel(d.crm_type)} #{d.crm_id}
                  {d.label && <div style={{ color: MUTED }}>{d.label}</div>}
                </td>
                <td style={cell}>
                  {deleteStateLabel(d.state)}
                  {d.restored_at && <div style={{ fontSize: 12, color: MUTED }}>{stamp(d.restored_at)}</div>}
                  {d.restore_detail && <div style={{ fontSize: 12, color: MUTED }}>{d.restore_detail}</div>}
                  {d.error && <div style={{ fontSize: 12, color: DANGER }}>{d.error}</div>}
                </td>
                <td style={{ ...cell, textAlign: 'right' }}>
                  {d.restorable && (
                    <button type="button" onClick={() => { setError(null); setResult(null); setAsking(d) }}
                      style={{ ...BUTTON, height: 32, padding: '0 12px', fontSize: 13 }}>
                      Restore
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {list.data && <Pager page={page} total={list.data.total} onPage={setPage} />}
      {asking && (
        <Confirm title={`Restore ${crmTypeLabel(asking.crm_type).toLowerCase()} #${asking.crm_id}?`}
          action="Restore" busy={restore.isPending}
          onCancel={() => setAsking(null)} onConfirm={() => restore.mutate(asking.id)}>
          {asking.label ? `“${asking.label}” ` : 'The record '}comes back in the CRM from its copy,
          with anything deleted in the same moment, and is recovered in Zuper.
        </Confirm>
      )}
    </section>
  )
}
