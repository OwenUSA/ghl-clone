import { useState } from 'react'
import { PageTabs } from '../components/PageTabs'
import { AgentsTab } from '../components/ai/AgentsTab'
import { AgentBuilder } from '../components/ai/AgentBuilder'
import { KnowledgeTab } from '../components/ai/KnowledgeTab'
import { TemplatesTab } from '../components/ai/TemplatesTab'
import { LogsTab } from '../components/ai/LogsTab'
import { PAGE_BG } from '../components/ai/aiUi'
import type { Me } from '../lib/auth'
import { aiTabs, canOpenAiAgents, type AiTab } from '../lib/aiAgents'

/**
 * AI Agents (2026-09-15): GoHighLevel's module, phase 1. One header with the shared tab
 * bar (PageTabs, as on every module); Agents · Knowledge Base · Templates · Agent Logs
 * (ADMIN). Opening an agent replaces the tab's content with its builder.
 */
export function AiAgentsPage({ user }: { user: Me }) {
  const tabs = aiTabs(user)
  const [tab, setTab] = useState<AiTab>('agents')
  const [agentId, setAgentId] = useState<number | null>(null)
  const [templateToUse, setTemplateToUse] = useState<number | null>(null)

  if (!canOpenAiAgents(user)) return null
  const active = tabs.some((t) => t.key === tab) ? tab : 'agents'

  return (
    <div className="flex min-w-0 flex-1 flex-col" style={{ height: '100vh', overflow: 'hidden',
      backgroundColor: PAGE_BG }}>
      <PageTabs title="AI Agents" label="AI Agents" active={agentId != null ? 'agents' : active}
        tabs={tabs.map((t) => ({ key: t.key, label: t.label,
          onSelect: () => { setAgentId(null); setTab(t.key) } }))} />
      {agentId != null ? (
        <AgentBuilder user={user} agentId={agentId} onBack={() => setAgentId(null)} />
      ) : active === 'agents' ? (
        <AgentsTab user={user} onOpen={setAgentId} templateToUse={templateToUse}
          onTemplateUsed={() => setTemplateToUse(null)} />
      ) : active === 'knowledge' ? (
        <KnowledgeTab user={user} />
      ) : active === 'templates' ? (
        <TemplatesTab user={user}
          onUse={(id) => { setTemplateToUse(id); setTab('agents') }} />
      ) : (
        <LogsTab user={user} />
      )}
    </div>
  )
}
