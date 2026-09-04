import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { IconChevronDown, IconPlay } from '../components/Icon'
import { getDashboard, listContacts } from '../lib/api'

/**
 * Launchpad — measured from captures/launchpad (1440x900):
 *   "Setup Guide"        15px/500 at x=256 y=82
 *   greeting             15px/500 at x=554
 *   "Your Foundational setup Progress." 13px/400 at x=552 y=126
 *   left nav groups      14px/600 at x=304, 40px pitch:
 *     Foundational setup / Marketing & lead generation /
 *     Sales & conversations / Website & monetization / Ecommerce
 *   percent chip         14px/600 (e.g. "30" + "%")
 *   task title           14px/500 at x=613
 *   task description     13px/400 at x=613, +20px below the title
 *   "Watch the Tutorial" 13px/500
 *   task CTA button      12px/500
 *   task card pitch      98px (210 -> 290 within a group; 510/608/706/804/902/1000)
 *
 * Progress is computed from real data rather than hard-coded, so the guide
 * reflects the actual state of the system.
 */
const GROUPS = [
  'Foundational setup',
  'Marketing & lead generation',
  'Sales & conversations',
  'Website & monetization',
  'Ecommerce',
]

type Task = {
  title: string
  desc: string
  cta: string
  group: string
  done?: boolean
  scope?: 'out'
}

export function LaunchpadPage({ onNavigate }: { onNavigate: (k: string) => void }) {
  const [group, setGroup] = useState(GROUPS[0])

  const contacts = useQuery({
    queryKey: ['contacts', 1, 1, '', 'created_at', 'desc'],
    queryFn: () => listContacts({ page: 1, page_size: 1 }),
  })
  const stats = useQuery({ queryKey: ['dashboard'], queryFn: () => getDashboard() })

  const tasks = useMemo<Task[]>(() => {
    const hasContacts = (contacts.data?.total ?? 0) > 0
    const hasOpps = (stats.data?.total ?? 0) > 0
    return [
      {
        group: 'Foundational setup',
        title: 'Create a new contact',
        desc: 'Add your first contact effortlessly and begin building your database.',
        cta: 'Go to Contacts',
        done: hasContacts,
      },
      {
        group: 'Foundational setup',
        title: 'Import and engage with all your contacts instantly',
        desc: 'Import your existing contacts from various platforms in one step.',
        cta: 'Import Existing Contacts',
        scope: 'out',
      },
      {
        group: 'Sales & conversations',
        title: 'Set up multi-channel communication & start engaging',
        desc: 'Connect your email, SMS, and phone services to reach customers.',
        cta: 'Go to Conversations',
      },
      {
        group: 'Sales & conversations',
        title: 'Book more appointments with automated scheduling',
        desc: 'Set up your calendar for automatic appointment booking.',
        cta: 'Go to Calendars',
      },
      {
        group: 'Sales & conversations',
        title: 'Accelerate deal closures with a streamlined pipeline',
        desc: 'Organize your sales pipeline to track every opportunity.',
        cta: 'Go to Opportunities',
        done: hasOpps,
      },
      {
        group: 'Marketing & lead generation',
        title: 'Generate new leads with a high-converting funnel',
        desc: 'Set up a high-converting funnel and form to capture leads.',
        cta: 'Not in v1',
        scope: 'out',
      },
      {
        group: 'Marketing & lead generation',
        title: 'Launch your first campaign to drive engagement',
        desc: 'Design and send your first email campaign to your audience.',
        cta: 'Not in v1',
        scope: 'out',
      },
      {
        group: 'Marketing & lead generation',
        title: 'Nurture leads with automated drip campaigns',
        desc: 'Set up automated email and SMS drip campaigns.',
        cta: 'Not in v1',
        scope: 'out',
      },
    ]
  }, [contacts.data, stats.data])

  const inGroup = tasks.filter((t) => t.group === group)
  const buildable = tasks.filter((t) => t.scope !== 'out')
  const pct = buildable.length
    ? Math.round((buildable.filter((t) => t.done).length / buildable.length) * 100)
    : 0

  const goto = (cta: string) => {
    if (cta.startsWith('Go to ')) onNavigate(cta.replace('Go to ', '').toLowerCase())
  }

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden"
      style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div className="flex shrink-0 items-center gap-6 bg-white px-4"
        style={{ height: 60, borderBottom: '1px solid rgb(234,236,240)' }}>
        <div style={{ fontSize: 15, fontWeight: 500, color: 'rgb(16,24,40)' }}>
          Setup Guide
        </div>
        <div style={{ fontSize: 15, fontWeight: 500, color: 'rgb(52,64,84)' }}>
          Hey Owen, here's your personalized setup guide
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-4 p-4">
        {/* left nav — measured 14px/600 group labels */}
        <div className="shrink-0 overflow-y-auto bg-white"
          style={{
            width: 'clamp(260px, 22vw, 320px)', borderRadius: 8,
            border: '1px solid rgb(234,236,240)', padding: 16,
          }}>
          <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
            Your Foundational setup Progress.
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex-1" style={{
              height: 8, borderRadius: 9999, backgroundColor: 'rgb(242,244,247)',
            }}>
              <div style={{
                width: `${pct}%`, height: 8, borderRadius: 9999,
                backgroundColor: 'rgb(0,78,235)',
              }} />
            </div>
            <span style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
              {pct}%
            </span>
          </div>

          <div className="mt-4">
            {GROUPS.map((g) => {
              const on = g === group
              const n = tasks.filter((t) => t.group === g).length
              return (
                <button
                  key={g}
                  onClick={() => setGroup(g)}
                  className="flex w-full items-center justify-between text-left"
                  style={{
                    height: 40, padding: '0 10px', borderRadius: 6,
                    fontSize: 14, fontWeight: 600,
                    color: on ? 'rgb(0,78,235)' : 'rgb(52,64,84)',
                    backgroundColor: on ? 'rgb(239,244,255)' : 'transparent',
                  }}
                >
                  {g}
                  <span style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>{n}</span>
                </button>
              )
            })}
          </div>
        </div>

        {/* task cards — measured 98px pitch */}
        <div className="min-w-0 flex-1 overflow-y-auto">
          {inGroup.length === 0 && (
            <div className="bg-white" style={{
              borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 24,
              fontSize: 14, color: 'rgb(102,112,133)',
            }}>
              Nothing in “{group}” — this area is out of scope for v1.
            </div>
          )}
          {inGroup.map((t) => (
            <div key={t.title} className="mb-3 bg-white"
              style={{
                borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 16,
              }}>
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)' }}>
                    {t.title}
                  </div>
                  <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 6 }}>
                    {t.desc}
                  </div>
                  <div className="mt-3 flex items-center gap-3">
                    <button
                      disabled={t.scope === 'out'}
                      onClick={() => goto(t.cta)}
                      title={t.scope === 'out' ? 'Out of scope for v1' : undefined}
                      style={{
                        height: 30, padding: '0 12px', borderRadius: 6,
                        fontSize: 12, fontWeight: 500,
                        color: t.scope === 'out' ? 'rgb(152,162,179)' : '#fff',
                        backgroundColor: t.scope === 'out'
                          ? 'rgb(242,244,247)' : 'rgb(0,78,235)',
                        cursor: t.scope === 'out' ? 'not-allowed' : 'pointer',
                      }}
                    >
                      {t.cta}
                    </button>
                    <span className="flex items-center gap-1"
                      style={{ fontSize: 13, fontWeight: 500, color: 'rgb(102,112,133)' }}>
                      <IconPlay size={14} color="rgb(102,112,133)" />
                      Watch the Tutorial
                    </span>
                  </div>
                </div>
                <span style={{
                  fontSize: 12, fontWeight: 600,
                  color: t.done ? 'rgb(18,183,106)' : 'rgb(102,112,133)',
                }}>
                  {t.done ? '100%' : '0%'}
                </span>
                <IconChevronDown size={16} color="rgb(152,162,179)" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
