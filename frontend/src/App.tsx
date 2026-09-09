import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sidebar } from './components/Sidebar'
import { ContactsPage } from './pages/ContactsPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { OpportunitiesPage } from './pages/OpportunitiesPage'
import { CalendarsPage } from './pages/CalendarsPage'
import { DashboardPage } from './pages/DashboardPage'
import { LaunchpadPage } from './pages/LaunchpadPage'
import { ReportingPage } from './pages/ReportingPage'
import { LoginPage } from './pages/LoginPage'
import { SettingsPage } from './pages/SettingsPage'
import { me } from './lib/auth'

const PLACEHOLDER: Record<string, string> = {
  launchpad: 'Launchpad',
  dashboard: 'Dashboard',
  conversations: 'Conversations',
  calendars: 'Calendars',
  opportunities: 'Opportunities',
  payments: 'Payments',
}

export default function App() {
  const [active, setActive] = useState('contacts')
  const qc = useQueryClient()

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
      <Sidebar active={active} onNavigate={setActive} user={user} />
      {active === 'contacts' ? (
        <ContactsPage user={user} />
      ) : active === 'conversations' ? (
        <ConversationsPage user={user} />
      ) : active === 'opportunities' ? (
        <OpportunitiesPage />
      ) : active === 'calendars' ? (
        <CalendarsPage />
      ) : active === 'dashboard' ? (
        <DashboardPage />
      ) : active === 'launchpad' ? (
        <LaunchpadPage onNavigate={setActive} />
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
