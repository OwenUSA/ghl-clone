import { useQuery } from '@tanstack/react-query'
import { getOpportunityZuper } from '../lib/api'
import {
  TONES, dateText, documentStatus, money, stamp, valueSourceSentence,
  type Tone, type ZuperDocument, type ZuperOpportunityMoney,
} from '../lib/zuper'
import { BODY, DIVIDER, FAINT, HEADING, MUTED, TEXT } from './opportunity/ui'

/**
 * The opportunity modal's "Quotes & invoices" (Zuper sync, 2026-09-16). READ-ONLY: Zuper owns
 * quotes, invoices and payments, and this is the CRM's cache of what Zuper last reported.
 *
 *   Quotes & invoices
 *   From Zuper — read only · Last synced Sep 16 2026, 9:17 AM (EDT)
 *   The card's value follows the invoices' total in Zuper.
 *   Quotes     Number | Status | Total | Date | Expires
 *   Invoices   Number | Status | Total | Balance | Date | Due
 *
 * The modal draws the nav item only when `showMoneyNav` says so (a linked job, or one with a
 * document); a card the reader cannot see is a 404 and draws nothing. OUR layout, from the
 * modal's own parts: the Photos tab's heading row, My Staff's table.
 */

export function Chip({ text, tone }: { text: string; tone: Tone }) {
  return (
    <span data-chip={text} style={{
      display: 'inline-block', padding: '2px 10px', borderRadius: 12, fontSize: 12,
      fontWeight: 500, whiteSpace: 'nowrap', color: TONES[tone].color,
      backgroundColor: TONES[tone].background,
    }}>{text}</span>
  )
}

const head: React.CSSProperties = {
  fontSize: 12, fontWeight: 500, color: BODY, textAlign: 'left', padding: '0 12px', height: 36,
  backgroundColor: 'rgb(249,250,251)', borderBottom: '1px solid ' + DIVIDER, whiteSpace: 'nowrap',
}
const cell: React.CSSProperties = {
  padding: '10px 12px', borderBottom: '1px solid ' + DIVIDER, fontSize: 14, color: TEXT,
  whiteSpace: 'nowrap',
}
const right: React.CSSProperties = { textAlign: 'right' }

export function ZuperMoneyPanel({ opportunityId, data: given }: {
  opportunityId: number
  /** The modal already fetched it to decide whether to draw the nav item. */
  data?: ZuperOpportunityMoney
}) {
  const query = useQuery({
    queryKey: ['opportunity-zuper', opportunityId],
    queryFn: () => getOpportunityZuper(opportunityId),
    enabled: !given,
    retry: false,
  })
  const data = given ?? query.data
  if (!data) {
    return <div style={{ fontSize: 14, color: FAINT }}>{query.isError ? (query.error as Error).message : 'Loading…'}</div>
  }
  const sentence = valueSourceSentence(data.value_source)
  const none = data.quotes.length === 0 && data.invoices.length === 0
  return (
    <div data-panel="zuper-money">
      <div style={{ ...HEADING, fontWeight: 600 }}>Quotes &amp; invoices</div>
      <div style={{ fontSize: 13, color: MUTED, marginTop: 4, paddingBottom: 12,
        borderBottom: '1px solid ' + DIVIDER }}>
        From Zuper — read only
        {data.last_synced_at && <> · Last synced {stamp(data.last_synced_at)}</>}
      </div>
      {data.sync !== 'on' && (
        <div data-testid="zuper-sync-paused" style={{ fontSize: 13, color: FAINT, marginTop: 10 }}>
          The Zuper sync is {data.sync === 'paused' ? 'paused' : 'off'}, so these may be out of date.
        </div>
      )}
      {sentence && (
        <div data-testid="zuper-value-source" style={{ fontSize: 14, color: BODY, marginTop: 12 }}>
          {sentence}
        </div>
      )}
      {none && (
        <div style={{ fontSize: 14, color: FAINT, marginTop: 18, textAlign: 'center' }}>
          No quotes or invoices in Zuper for this job yet.
        </div>
      )}
      {data.quotes.length > 0 && (
        <DocTable title="Quotes" label="Quotes"
          columns={['Number', 'Status', 'Total', 'Date', 'Expires']}
          rows={data.quotes} render={(d) => [
            d.number ?? '—', <StatusChip key="s" d={d} />, money(d.total_cents),
            dateText(d.issued_on), dateText(d.due_on),
          ]} />
      )}
      {data.invoices.length > 0 && (
        <DocTable title="Invoices" label="Invoices"
          columns={['Number', 'Status', 'Total', 'Balance', 'Date', 'Due']}
          rows={data.invoices} render={(d) => [
            d.number ?? '—', <StatusChip key="s" d={d} />, money(d.total_cents),
            money(d.balance_cents), dateText(d.issued_on), dateText(d.due_on),
          ]} />
      )}
    </div>
  )
}

function StatusChip({ d }: { d: ZuperDocument }) {
  return <Chip {...documentStatus(d.status)} />
}

const MONEY_COLUMNS = new Set(['Total', 'Balance'])

function DocTable({ title, label, columns, rows, render }: {
  title: string; label: string; columns: string[]; rows: ZuperDocument[]
  render: (d: ZuperDocument) => React.ReactNode[]
}) {
  return (
    <section style={{ marginTop: 20 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: BODY }}>{title} ({rows.length})</div>
      <div style={{ overflowX: 'auto', marginTop: 8, border: '1px solid ' + DIVIDER, borderRadius: 4 }}>
        <table aria-label={label} style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            {columns.map((c) => (
              <th key={c} style={{ ...head, ...(MONEY_COLUMNS.has(c) ? right : {}) }}>{c}</th>
            ))}
          </tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id} data-document={d.id}>
                {render(d).map((v, i) => (
                  <td key={i} style={{ ...cell, ...(MONEY_COLUMNS.has(columns[i]) ? right : {}) }}>{v}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
