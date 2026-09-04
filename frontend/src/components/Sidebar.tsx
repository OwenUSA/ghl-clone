/**
 * App shell sidebar.
 *
 * Every value here is measured from captures/contacts/*__1440x900__top__*.json,
 * not eyeballed from a screenshot:
 *   aside      w=224  bg rgb(45,55,72)  h=100vh
 *   nav row    h=36   radius 6px  padding 8px/16px  pitch 36px
 *   active row bg rgb(26,32,44)
 *   label      14px / weight 500 / line-height 20px / white
 *   location   12px / weight 500 (name) and 400 (city) / white
 *   68.8px gap between Payments and AI Agents -> section divider
 */
import {
  IconAward, IconCalendar, IconCard, IconChart, IconChat, IconGrid, IconImage,
  IconMegaphone, IconPlay, IconRocket, IconSettings, IconSearch, IconSparkle,
  IconStar, IconStore, IconUser, IconUsers, IconDoc,
} from './Icon'
import { logout } from '../lib/auth'

const PRIMARY = [
  { key: 'launchpad', label: 'Launchpad', Icon: IconRocket },
  { key: 'dashboard', label: 'Dashboard', Icon: IconGrid },
  { key: 'conversations', label: 'Conversations', Icon: IconChat },
  { key: 'calendars', label: 'Calendars', Icon: IconCalendar },
  { key: 'contacts', label: 'Contacts', Icon: IconUser },
  { key: 'opportunities', label: 'Opportunities', Icon: IconUsers },
  { key: 'payments', label: 'Payments', Icon: IconCard },
  { key: 'reporting', label: 'Reporting', Icon: IconChart },
]

// Out of scope for this build (DECISIONS.md) but present in GHL's shell.
// Rendered dimmed so the shell reads the same and the omission stays visible.
type IconCmp = (p: { size?: number; color?: string }) => React.ReactElement

const SECONDARY: { label: string; Icon: IconCmp }[] = [
  { label: 'AI Agents', Icon: IconSparkle },
  { label: 'Marketing', Icon: IconMegaphone },
  { label: 'Automation', Icon: IconPlay },
  { label: 'Sites', Icon: IconStore },
  { label: 'Memberships', Icon: IconAward },
  { label: 'Media Storage', Icon: IconImage },
  { label: 'Reputation', Icon: IconStar },
  { label: 'App Marketplace', Icon: IconDoc },
]

export function Sidebar({
  active,
  onNavigate,
  user,
}: {
  active: string
  onNavigate: (key: string) => void
  user?: { name: string; role: string }
}) {
  return (
    <aside
      className="relative flex flex-col shrink-0"
      style={{ width: 224, height: '100vh', backgroundColor: 'rgb(45,55,72)' }}
    >
      <div className="flex items-center gap-2 px-4 pt-6 pb-2">
        <div
          className="flex items-center justify-center shrink-0"
          style={{
            width: 28,
            height: 28,
            borderRadius: 6,
            backgroundColor: 'rgb(26,32,44)',
          }}
        >
          <span style={{ color: '#fff', fontSize: 12, fontWeight: 600 }}>DT</span>
        </div>
        <div className="min-w-0">
          <div
            className="truncate"
            style={{ color: '#fff', fontSize: 12, fontWeight: 500, lineHeight: '16px' }}
          >
            Dream Team Roofing
          </div>
          <div
            className="truncate"
            style={{ color: '#fff', fontSize: 12, fontWeight: 400, lineHeight: '16px' }}
          >
            Bradenton, FL
          </div>
        </div>
      </div>

      <div className="px-4 pb-2 pt-3">
        <div
          className="flex items-center gap-2 px-2"
          style={{
            height: 32,
            borderRadius: 6,
            backgroundColor: 'rgb(26,32,44)',
            color: 'rgb(152,162,179)',
            fontSize: 14,
          }}
        >
          <IconSearch size={16} color="rgb(152,162,179)" />
          <span className="flex-1">Search</span>
          {/* measured: GHL shows a "ctrlK" hint badge inside the search row */}
          <span style={{
            fontSize: 11, color: 'rgb(152,162,179)',
            backgroundColor: 'rgb(45,55,72)', borderRadius: 4, padding: '1px 5px',
          }}>ctrlK</span>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-0 pt-2">
        {PRIMARY.map((item) => {
          const isActive = item.key === active
          return (
            <button
              key={item.key}
              onClick={() => onNavigate(item.key)}
              className="flex w-full items-center gap-3 text-left"
              style={{
                height: 36,
                borderRadius: 6,
                paddingTop: 8,
                paddingBottom: 8,
                paddingLeft: 16,
                paddingRight: 16,
                color: '#fff',
                fontSize: 14,
                fontWeight: 500,
                lineHeight: '20px',
                backgroundColor: isActive ? 'rgb(26,32,44)' : 'transparent',
              }}
            >
              <item.Icon size={18} color="#fff" />
              {item.label}
            </button>
          )
        })}

        {/* Measured 68.8px pitch here vs 36px elsewhere */}
        <div style={{ height: 32.8 }} />

        {SECONDARY.map((item) => (
          <div
            key={item.label}
            title="Out of scope for v1"
            className="flex w-full items-center gap-3"
            style={{
              height: 36,
              paddingLeft: 16,
              paddingRight: 16,
              color: 'rgba(255,255,255,0.35)',
              fontSize: 14,
              fontWeight: 500,
              lineHeight: '20px',
            }}
          >
            <item.Icon size={18} color="rgba(255,255,255,0.35)" />
            {item.label}
          </div>
        ))}
      </nav>

      {/* Settings was a label with nothing behind it; it is now a real target,
          because it is where API tokens are minted and revoked. */}
      <button
        onClick={() => onNavigate('settings')}
        className="flex items-center gap-3 w-full"
        style={{
          height: 36,
          paddingLeft: 16,
          color: '#fff',
          fontSize: 14,
          fontWeight: 500,
          background: active === 'settings' ? 'rgb(26,32,44)' : 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <IconSettings size={18} color="#fff" />
        Settings
      </button>

      {user && (
        <div
          style={{
            padding: '10px 16px 16px',
            borderTop: '1px solid rgba(255,255,255,0.08)',
            marginTop: 8,
          }}
        >
          <div style={{ color: '#fff', fontSize: 13, fontWeight: 500 }}>
            {user.name}
          </div>
          <div
            style={{
              color: 'rgb(152,162,179)',
              fontSize: 12,
              marginTop: 2,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span>{user.role}</span>
            <button
              onClick={async () => {
                await logout()
                // Full reload rather than clearing the cache by hand: it
                // guarantees no fetched record outlives the session.
                window.location.reload()
              }}
              style={{
                border: 'none',
                background: 'transparent',
                color: 'rgb(152,162,179)',
                fontSize: 12,
                cursor: 'pointer',
                padding: 0,
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}
