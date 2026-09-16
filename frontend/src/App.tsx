import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sidebar } from './components/Sidebar'
import { SearchPalette } from './components/SearchPalette'
import { Softphone } from './components/Softphone'
import { InCallWindow } from './components/InCallWindow'
import { StatusIndicator } from './components/StatusIndicator'
import { SoftphoneProvider } from './lib/softphoneContext'
import { ContactsPage } from './pages/ContactsPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { OpportunitiesPage } from './pages/OpportunitiesPage'
import { CalendarsPage } from './pages/CalendarsPage'
import { DashboardPage } from './pages/DashboardPage'
import { ReportingPage } from './pages/ReportingPage'
import { LoginPage } from './pages/LoginPage'
import { SettingsPage } from './pages/SettingsPage'
import { AiAgentsPage } from './pages/AiAgentsPage'
import { AiAlertBell } from './components/AiAlertBell'
import { canOpenAiAgents } from './lib/aiAgents'
import { me } from './lib/auth'
import { viewFor } from './lib/access'
import { ChangePasswordScreen } from './pages/ChangePasswordScreen'
import { viewFromPath } from './lib/settingsSections'
import type { Focus } from './lib/focus'
import { registerOpenRecord } from './lib/openRecord'
import { recordFromLocation, withoutRecordParam } from './lib/zuper'

// Paths that used to mean something and still turn up in bookmarks and pasted
// links. Launchpad was removed from the product on 2026-09-09 and Payments on
// 2026-09-10; the owner asked that each land on Dashboard rather than dead-end,
// so a link someone saved keeps working. There is no router yet (DECISIONS.md),
// so this is the whole of the app's URL handling: read the path once at boot,
// then rewrite it away.
const RETIRED_PATHS: Record<string, string> = {
  '/launchpad': 'dashboard',
  '/payments': 'dashboard',
}

// The view the app opens on. Was 'contacts'; the owner moved it to Dashboard
// when Launchpad was removed, so the first screen after signing in is a summary
// rather than a 268-row table.
const DEFAULT_VIEW = 'dashboard'

function initialView(): string {
  const path = window.location.pathname.replace(/\/+$/, '').toLowerCase() || '/'
  const retired = RETIRED_PATHS[path]
  if (retired) {
    // Replace, not push: Back must go where the user came from, not back to a
    // path that no longer exists and would redirect again.
    window.history.replaceState(null, '', '/')
    return retired
  }
  // Two paths are real links now, kept in the address bar so a reload stays put:
  // /opportunities?pipeline=<id> (the Pipelines tab's Copy link) and
  // /settings/custom-fields (where Custom Fields moved to on 2026-09-13).
  return viewFromPath(path) ?? DEFAULT_VIEW
}

export default function App() {
  const [active, setActive] = useState(initialView)

  // Leaving the view a deep link opened drops the link from the address bar, so a
  // reload after navigating elsewhere does not jump back to it.
  useEffect(() => {
    if (viewFromPath(window.location.pathname) && viewFromPath(window.location.pathname) !== active) {
      window.history.replaceState(null, '', '/')
    }
  }, [active])
  const [searchOpen, setSearchOpen] = useState(false)
  const qc = useQueryClient()

  // What the palette asked a page to open. `n` makes every request a new object
  // even when it names the same record, so searching for the same contact twice
  // in a row reopens the panel the user just closed instead of doing nothing.
  const [focus, setFocus] = useState<(Focus & { view: string }) | null>(null)
  const seq = useRef(0)
  const openRecord = useCallback((view: string, id: number) => {
    seq.current += 1
    setActive(view)
    setFocus({ view, id, n: seq.current })
  }, [])
  const focusFor = (view: string) => (focus?.view === view ? focus : null)
  useEffect(() => registerOpenRecord(openRecord), [openRecord])

  // The "CRM Link" the Zuper sync writes into Zuper (2026-09-16): /opportunities?opportunity=<id>
  // and /contacts?contact=<id> open that record, through the palette's own path. Read once at
  // boot; the parameter is then dropped so a reload after closing the record does not reopen
  // it. Signing in first is fine: the request waits in `focus` for the page to mount.
  useEffect(() => {
    const link = recordFromLocation(window.location.pathname, window.location.search)
    if (!link) return
    window.history.replaceState(null, '',
      withoutRecordParam(window.location.pathname, window.location.search))
    openRecord(link.view, link.id)
  }, [openRecord])

  // ctrl+K, and cmd+K on a Mac. Bound on the window so it works from anywhere,
  // including with the caret inside a page's own filter box -- which is exactly
  // when someone reaches for it. preventDefault stops Chrome's own ctrl+K
  // (focus the address bar as a search) from swallowing it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // The whole app is gated on this one query. There is no router yet
  // (DECISIONS.md), so a conditional render at the root is the whole mechanism.
  const session = useQuery({
    queryKey: ['me'],
    queryFn: me,
    retry: false,
    refetchInterval: false,
    staleTime: Infinity,
  })

  // api.ts fires this when a request 401s and the token could not be renewed —
  // for instance after "log out everywhere" from another browser.
  useEffect(() => {
    const drop = () => qc.setQueryData(['me'], null)
    window.addEventListener('ghl:unauthorized', drop)
    return () => window.removeEventListener('ghl:unauthorized', drop)
  }, [qc])

  if (session.isLoading) {
    return <div style={{ height: '100vh', width: '100vw' }} />
  }

  if (session.isError || !session.data) {
    return <LoginPage onSignedIn={() => session.refetch()} />
  }

  const user = session.data.user

  // My Staff (2026-09-15): an admin created this account or reset its password. The API
  // answers nothing else until a new one is chosen, so nothing else is drawn either.
  if (user.must_change_password) {
    return <ChangePasswordScreen user={user} onChanged={() => session.refetch()} />
  }

  // "Only assigned data": Dashboard and Reporting are not a restricted user's to open —
  // including the landing view and a pasted link — so they land on their jobs.
  const view = viewFor(user, active)

  // The softphone is mounted HERE, around the whole shell, for two reasons: a
  // registration is scarce (the operator AOR holds one contact, so the hook must
  // exist exactly once), and a call rings the browser rather than a screen -- the
  // incoming card has to appear whatever page is open. Nothing about it belongs to
  // Conversations. See components/Softphone.tsx.
  //
  // The status dot sits beside it for the same reason (2026-09-14): it is on every page,
  // top right, and it reads the one softphone rather than starting another.
  return (
    <SoftphoneProvider>
    <div className="flex h-screen w-screen overflow-hidden">
      <Softphone />
      <StatusIndicator />
      <AiAlertBell user={user} />
      {/* The one in-call window, for every call this browser is on (2026-09-14). */}
      <InCallWindow user={user} />
      <Sidebar
        active={view}
        onNavigate={setActive}
        onOpenSearch={() => setSearchOpen(true)}
        user={user}
      />
      {searchOpen && (
        <SearchPalette onClose={() => setSearchOpen(false)} onOpen={openRecord} />
      )}
      {view === 'contacts' ? (
        <ContactsPage user={user} focus={focusFor('contacts')} />
      ) : view === 'conversations' ? (
        <ConversationsPage user={user} focus={focusFor('conversations')} />
      ) : view === 'opportunities' ? (
        <OpportunitiesPage
          user={user}
          focus={focusFor('opportunities')}
          onNavigate={setActive}
        />
      ) : view === 'calendars' ? (
        <CalendarsPage user={user} />
      ) : view === 'dashboard' ? (
        <DashboardPage />
      ) : view === 'reporting' ? (
        <ReportingPage />
      ) : view === 'ai-agents' && canOpenAiAgents(user) ? (
        // AI Agents (2026-09-15) is ADMIN / DISPATCHER only, never a restricted user: anyone
        // else asking for it (a pasted /ai-agents link) falls through to the landing view.
        <AiAgentsPage user={user} />
      ) : view === 'settings' ? (
        <SettingsPage user={user} />
      ) : (
        // Every sidebar key above renders a real page. Payments was the last
        // key that fell through to a "not built yet" card, and it went with the
        // nav row on 2026-09-10, so this branch is now only reachable by an
        // unknown key -- send that to the landing view rather than a blank pane.
        viewFor(user, 'dashboard') === 'dashboard'
          ? <DashboardPage />
          : <OpportunitiesPage user={user} focus={null} onNavigate={setActive} />
      )}
    </div>
    </SoftphoneProvider>
  )
}
