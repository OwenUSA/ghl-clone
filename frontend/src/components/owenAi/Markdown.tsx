import { blocks, cellLines, type Inline } from '../../lib/dispatch'
import { codeSpans, linkSpans, splitFences } from '../../lib/owenAi'
import { BLUE, LINE, MONO, MUTED } from './tokens'

/**
 * An answer's Markdown, as React elements — never as HTML, so an answer cannot inject markup.
 * Code fences are split out first (lib/owenAi `splitFences`); the rest goes through the
 * Dispatch assistant's parser (lib/dispatch `blocks`: headings, **bold**, lists, tables), with
 * `inline code` added on top.
 */
export function Markdown({ text }: { text: string }) {
  return (
    <>
      {splitFences(text).map((s, i) => s.kind === 'code' ? (
        <pre key={i} style={{ margin: '0 0 12px', padding: '8px 10px', background: 'rgb(243,244,246)',
          border: `1px solid ${LINE}`, borderRadius: 6, fontSize: 12, lineHeight: '18px', overflowX: 'auto',
          fontFamily: MONO, color: 'rgb(52,64,84)' }}>{s.text}</pre>
      ) : (
        <Blocks key={i} text={s.text} />
      ))}
    </>
  )
}

function Parts({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.flatMap((p, i) => codeSpans(p.text).map((c, j) => {
        const key = `${i}-${j}`
        if (c.code) {
          return <code key={key} style={{ fontFamily: MONO, fontSize: '0.88em', background: 'rgb(243,244,246)',
            borderRadius: 4, padding: '1px 4px', fontWeight: p.bold ? 600 : undefined }}>{c.text}</code>
        }
        return <Links key={key} text={c.text} bold={p.bold} />
      }))}
    </>
  )
}

/** Links (2026-11-07): an inspection report on this server is a download button; an https URL
    opens in a new tab; any other target is shown as its label only (lib/owenAi `linkSpans`). */
function Links({ text, bold }: { text: string; bold: boolean }) {
  return (
    <>
      {linkSpans(text).map((s, i) => {
        if (s.kind === 'report') {
          return (
            <a key={i} href={s.href} download
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, margin: '2px 6px 2px 0',
                padding: '5px 12px', borderRadius: 8, background: BLUE, color: '#fff', fontSize: 13,
                fontWeight: 600, textDecoration: 'none', lineHeight: '18px' }}>
              <span aria-hidden="true">⤓</span>{s.text}
            </a>
          )
        }
        if (s.kind === 'link') {
          return <a key={i} href={s.href} target="_blank" rel="noopener noreferrer"
            style={{ color: BLUE, fontWeight: bold ? 600 : undefined }}>{s.text}</a>
        }
        return bold ? <strong key={i}>{s.text}</strong> : <span key={i}>{s.text}</span>
      })}
    </>
  )
}

function Blocks({ text }: { text: string }) {
  return (
    <>
      {blocks(text).map((b, i) => b.kind === 'p' ? (
        <p key={i} style={{ margin: '0 0 10px' }}><Parts parts={b.parts} /></p>
      ) : b.kind === 'h' ? (
        <div key={i} style={{ fontWeight: 600, margin: '12px 0 6px' }}><Parts parts={b.parts} /></div>
      ) : b.kind === 'table' ? (
        <div key={i} style={{ overflowX: 'auto', margin: '0 0 12px' }} role="region" aria-label="Table">
          <table style={{ borderCollapse: 'collapse', fontSize: 13, lineHeight: '18px', minWidth: '100%' }}>
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
                  <td key={k} style={{ padding: '6px 8px', borderBottom: `1px solid ${LINE}`, verticalAlign: 'top' }}>
                    {cellLines(r[k] ?? []).map((line, n) => (
                      <div key={n} style={n ? { marginTop: 2 } : undefined}><Parts parts={line} /></div>
                    ))}</td>
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
