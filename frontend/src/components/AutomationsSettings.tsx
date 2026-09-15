import { useQuery } from '@tanstack/react-query'
import { listAutomations } from '../lib/api'
import { DIVIDER, ErrorLine, FAINT, MUTED, TEXT } from './opportunity/ui'

/**
 * Settings → Automations (2026-09-15). Read-only, for everyone.
 *
 * The four hard-coded rules (backend/app/automations.py) and whether each is on, read
 * from the server's own flags through `GET /api/automations` — so the page can never say
 * something the code does not do. There are deliberately NO switches: the owner's decision
 * is that nothing texts a customer by itself, so a toggle would be either a control that
 * silently does nothing or a way round that decision. Each rule shows On / Off and why.
 *
 * OUR design: GoHighLevel's Automation module is a workflow builder this product does not
 * have (DECISIONS.md, 2026-08-14). Styled like Settings → CompanyCam.
 */
export function AutomationsSettings() {
  const rules = useQuery({ queryKey: ['automations'], queryFn: listAutomations })
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 32 }}>
      <section style={{ maxWidth: 860 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: TEXT }}>Automations</div>
        <div style={{ fontSize: 14, color: FAINT, marginTop: 4, lineHeight: 1.5 }}>
          No text goes to a customer unless a person sends it. These are the built-in rules and
          what each one does today. AI agents are separate: each has its own Off, Suggest or
          Auto-pilot setting on the AI Agents page.
        </div>
        <ErrorLine error={rules.error ? (rules.error as Error).message : null} />
        <div role="list" aria-label="Automation rules" style={{ marginTop: 16 }}>
          {rules.data?.rules.map((r) => (
            <div role="listitem" key={r.key} data-rule={r.key} data-enabled={r.enabled}
              style={{ border: '1px solid ' + DIVIDER, borderRadius: 8, padding: '12px 16px',
                marginTop: 12, backgroundColor: '#fff' }}>
              <div className="flex items-center gap-3">
                <div style={{ fontSize: 14, fontWeight: 600, color: TEXT }}>{r.name}</div>
                <span data-testid="rule-state" style={{
                  marginLeft: 'auto', padding: '2px 10px', borderRadius: 12, fontSize: 12,
                  fontWeight: 500,
                  color: r.enabled ? 'rgb(2,122,72)' : 'rgb(71,84,103)',
                  backgroundColor: r.enabled ? 'rgb(236,253,243)' : 'rgb(242,244,247)',
                }}>{r.enabled ? 'On' : 'Off'}</span>
              </div>
              <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>
                When: {r.trigger}{r.texts_customer ? ' · texts the customer' : ' · internal only'}
              </div>
              {r.reason && (
                <div data-testid="rule-reason" style={{ fontSize: 13, color: TEXT, marginTop: 6 }}>
                  {r.reason}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
