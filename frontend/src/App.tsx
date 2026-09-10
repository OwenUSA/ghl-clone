import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sidebar } from './components/Sidebar'
import { SearchPalette } from './components/SearchPalette'
import { ContactsPage } from './pages/ContactsPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { OpportunitiesPage } from './pages/OpportunitiesPage'
import { CalendarsPage } from './pages/CalendarsPage'
import { DashboardPage } from './pages/DashboardPage'
import { ReportingPage } from './pages/ReportingPage'
import { LoginPage } from './pages/LoginPage'
import { SettingsPage } from './pages/SettingsPage'
import { me } from './lib/auth'
import type { Focus } from './lib/focus'

const PLACEHOLDER: Record<string, string> = {
  dashboard: 'Dashboard',
  conversations: 'Conversations',
  calendars: 'Calendars',
  opportunities: 'Opportunities',
  payments: 'Payments',
}

// Paths that used to mean something and still turn up in bookmarks and pasted
// links. Launchpad was removed from the product on 2026-09-09; the owner asked
// that /launchpad land on Dashboard rather than dead-end, so the link keeps
// working. There is no router yet (DECISIONS.md), so this is the whole of the
// app's URL handling: read the path once at boot, then rewrite it away.
const RETIRED_PATHS: Record<string, string> = {
  '/launchpad': 'dashboard',
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
  return DEFAULT_VIEW
}

export default function App() {
  const [active, setActive] = useState(initialView)
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

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar
        active={active}
        onNavigate={setActive}
        onOpenSearch={() => setSearchOpen(true)}
        user={user}
      />
      {searchOpen && (
        <SearchPalette onClose={() => setSearchOpen(false)} onOpen={openRecord} />
      )}
      {active === 'contacts' ? (
        <ContactsPage user={user} focus={focusFor('contacts')} />
      ) : active === 'conversations' ? (
        <ConversationsPage user={user} focus={focusFor('conversations')} />
      ) : active === 'opportunities' ? (
        <OpportunitiesPage user={user} focus={focusFor('opportunities')} />
      ) : active === 'calendars' ? (
        <CalendarsPage user={user} />
      ) : active === 'dashboard' ? (
        <DashboardPage />
      ) : active === 'reporting' ? (
        <ReportingPage />
      ) : active === 'settings' ? (
        <SettingsPage user={user} />
      ) : (
        <div className="flex flex-1 items-center justify-center" style={{ fontSize: 14 }}>
          {PLACEHOLDER[active]} — not built yet
        </div>
      )}
    </div>
  )
}
