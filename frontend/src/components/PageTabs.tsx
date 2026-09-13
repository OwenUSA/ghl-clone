import { tabLook, type PageTab } from '../lib/pageTabs'

/**
 * The page header every module shares: the module title, then its tabs.
 *
 * Extracted verbatim from the Opportunities bar rebuilt on 2026-09-13
 * (refs/round3/23) so Opportunities, Contacts, Conversations and Calendars draw
 * one look. Each page keeps its own tab list and what each tab does — a tab with
 * no `onSelect` is a label, as it was before the bar was shared.
 */
export function PageTabs({ title, label, tabs, active }: {
  title: string
  /** aria-label for the tablist. */
  label: string
  tabs: PageTab[]
  active: string
}) {
  return (
    <div
      className="flex shrink-0 items-end gap-6 bg-white px-4"
      style={{ height: 90, borderBottom: '1px solid rgb(234,236,240)' }}
    >
      <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)', paddingBottom: 10,
        lineHeight: '24px' }}>
        {title}
      </div>
      <div role="tablist" aria-label={label} className="flex items-stretch"
        style={{ height: 44, gap: 6, marginLeft: -2 }}>
        {tabs.map((t) => {
          const on = active === t.key
          const style = {
            fontSize: 14,
            fontWeight: 500,
            padding: '0 7px',
            borderTop: '2px solid transparent',
            ...tabLook(on, !!t.blocked),
          }
          // A label that never did anything stays a label: no pointer, no button.
          if (!t.onSelect && !t.blocked) {
            return (
              <span key={t.key} role="tab" aria-selected={on} className="flex items-center"
                style={{ ...style, cursor: 'default' }}>
                {t.label}
              </span>
            )
          }
          return (
            <button
              key={t.key}
              role="tab"
              aria-selected={on}
              onClick={() => !t.blocked && t.onSelect?.()}
              disabled={!!t.blocked}
              title={t.blocked}
              style={{ ...style, cursor: t.blocked ? 'not-allowed' : 'pointer' }}
            >
              {t.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
