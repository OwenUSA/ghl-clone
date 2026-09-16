import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import {
  aiAgent, aiCatalogue, aiConnectionNames, aiFolders, aiKbs, getContact, listContacts, listPipelines,
  listUsers, previewAiPrompt, publishAiAgent, setAiAgentMode, tryAiAgent, updateAiAgent,
  type AiAgentDetail, type AiWould,
} from '../../lib/api'
import type { Me } from '../../lib/auth'
import {
  addItem, BUILDER_SECTIONS, DAYS, dayLabel, draftForSave, formatCost, formatLatency, formatTokens,
  isAiAdmin, MODE_LABEL, MODE_TONE, modeLabel, modesFor, moveItem, needsConfirm, newTrigger,
  outcomeLabel, removeItem, sameDraft, scheduleSummary, setItem, stamp, toggle,
  TRIGGER_LABEL, triggerSummary, wouldText,
  type BuilderSection, type Draft, type Mode, type Trigger,
} from '../../lib/aiAgents'
import { IconChevronLeft, IconSparkle } from '../Icon'
import { formatPhone } from '../../lib/phone'
import { useOurLine } from '../../lib/useOurLine'
import { IconClose, IconPlus, IconTrashOutline } from '../PipelineIcons'
import {
  BLUE, BODY, BORDER, Chip, Confirm, FAINT, Field, INK, INPUT, LABEL, LINE, MUTED, Notice,
  PAGE_BG, primarySmall, PRIMARY_TINT, SectionTitle, smallButton, Switch, TEXT, textarea, textButton,
} from './aiUi'

type Props = { user: Me; agentId: number; onBack: () => void }

/**
 * The agent builder (2026-09-15). A top bar (back, name, mode, Save draft, Publish), the
 * opportunity modal's left section nav (refs/opps/22), the form, and a right-hand Try it
 * panel for an ADMIN. Editing saves a DRAFT; Publish freezes a version. A dispatcher sees
 * the same sections as plain text, with no control that would be refused.
 */
export function AgentBuilder({ user, agentId, onBack }: Props) {
  const detail = useQuery({ queryKey: ['ai-agent', agentId], queryFn: () => aiAgent(agentId) })
  if (detail.isError) {
    return (
      <div style={{ padding: 24 }}>
        <button type="button" onClick={onBack} style={textButton}>Back to agents</button>
        <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>
          {(detail.error as Error).message}
        </div>
      </div>
    )
  }
  if (!detail.data) return <div style={{ padding: 24, fontSize: 13, color: MUTED }}>Loading…</div>
  return <BuilderLoaded key={detail.data.id} user={user} agent={detail.data} onBack={onBack} />
}

function BuilderLoaded({ user, agent, onBack }: { user: Me; agent: AiAgentDetail; onBack: () => void }) {
  const qc = useQueryClient()
  const admin = isAiAdmin(user) && agent.can_edit
  const [draft, setDraft] = useState<Draft>(agent.draft)
  const [name, setName] = useState(agent.name)
  const [description, setDescription] = useState(agent.description ?? '')
  const [folderId, setFolderId] = useState<number | null>(agent.folder_id)
  const [section, setSection] = useState<BuilderSection>('basics')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmAuto, setConfirmAuto] = useState(false)

  const dirty = !sameDraft(draftForSave(draft), draftForSave(agent.draft)) || name !== agent.name
    || description !== (agent.description ?? '') || folderId !== agent.folder_id
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }))

  const refreshAll = (a: AiAgentDetail) => {
    qc.setQueryData(['ai-agent', a.id], a)
    qc.invalidateQueries({ queryKey: ['ai-agents'] })
  }

  const save = useMutation({
    mutationFn: () => updateAiAgent(agent.id, { name: name.trim(), description: description.trim() || null,
      folder_id: folderId, draft: draftForSave(draft) }),
    onSuccess: (a) => { refreshAll(a); setError(null); setNotice('Draft saved. The published version is unchanged.') },
    onError: (e: Error) => { setNotice(null); setError(e.message) },
  })
  const publish = useMutation({
    mutationFn: async () => {
      if (dirty) await updateAiAgent(agent.id, { name: name.trim(), description: description.trim() || null,
        folder_id: folderId, draft: draftForSave(draft) })
      return publishAiAgent(agent.id)
    },
    onSuccess: (a) => { refreshAll(a); setError(null); setNotice(`Published version ${a.published_version}.`) },
    onError: (e: Error) => { setNotice(null); setError(e.message) },
  })
  const mode = useMutation({
    mutationFn: ({ to, confirm }: { to: Mode; confirm: boolean }) => setAiAgentMode(agent.id, to, confirm),
    onSuccess: (a) => { setConfirmAuto(false); refreshAll(a); setError(null); setNotice(`“${a.name}” is ${modeLabel(a.mode)}.`) },
    onError: (e: Error) => { setConfirmAuto(false); setNotice(null); setError(e.message) },
  })
  const chooseMode = (to: Mode) => {
    if (to === agent.mode) return
    if (needsConfirm(agent.mode, to)) setConfirmAuto(true)
    else mode.mutate({ to, confirm: false })
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-12 bg-white"
        style={{ padding: '10px 16px', borderBottom: `1px solid ${LINE}` }}>
        <button type="button" onClick={onBack} className="flex items-center gap-4"
          style={{ fontSize: 13, color: MUTED }}>
          <IconChevronLeft size={16} color={MUTED} /> Agents
        </button>
        <div style={{ width: 1, height: 20, backgroundColor: LINE }} />
        <IconSparkle size={18} color={BLUE} />
        <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>{name || agent.name}</div>
        <Chip text={agent.published_version != null ? `Published v${agent.published_version}` : 'Draft — not published'}
          fg={agent.published_version != null ? 'rgb(2,122,72)' : 'rgb(71,84,103)'}
          bg={agent.published_version != null ? 'rgb(236,253,243)' : 'rgb(242,244,247)'} />
        {(dirty || (agent.published_version != null && agent.has_unpublished_changes)) && admin && (
          <span style={{ fontSize: 12, color: 'rgb(181,71,8)' }}>
            {dirty ? 'Unsaved changes' : 'Unpublished changes'}
          </span>
        )}
        <div className="ml-auto flex items-center gap-8">
          <span style={{ fontSize: 13, color: MUTED }}>Mode</span>
          {admin ? (
            <div role="radiogroup" aria-label="Mode" className="flex"
              style={{ border: `1px solid ${BORDER}`, borderRadius: 6, overflow: 'hidden' }}>
              {modesFor(user).map((m) => (
                <button key={m} type="button" role="radio" aria-checked={agent.mode === m}
                  onClick={() => chooseMode(m)}
                  style={{ height: 30, padding: '0 12px', fontSize: 13, fontWeight: 500,
                    color: agent.mode === m ? MODE_TONE[m].fg : BODY,
                    backgroundColor: agent.mode === m ? MODE_TONE[m].bg : '#fff',
                    borderLeft: m === 'off' ? 'none' : `1px solid ${BORDER}` }}>
                  {MODE_LABEL[m]}
                </button>
              ))}
            </div>
          ) : (
            <>
              <Chip text={modeLabel(agent.mode)} {...(MODE_TONE[agent.mode] ?? MODE_TONE.off)} />
              {agent.mode !== 'off' && modesFor(user).includes('off') && (
                <button type="button" onClick={() => mode.mutate({ to: 'off', confirm: false })} style={smallButton}>
                  Switch off
                </button>
              )}
            </>
          )}
          {admin && (
            <>
              <button type="button" onClick={() => save.mutate()} disabled={!dirty || save.isPending}
                style={{ ...smallButton, opacity: !dirty || save.isPending ? 0.5 : 1 }}>
                Save draft
              </button>
              <button type="button" onClick={() => publish.mutate()} disabled={publish.isPending}
                style={{ ...primarySmall, opacity: publish.isPending ? 0.6 : 1 }}>
                Publish
              </button>
            </>
          )}
        </div>
      </div>
      <div style={{ padding: '0 16px' }}>
        <Notice error={error} notice={notice} onDismiss={() => { setError(null); setNotice(null) }} />
        {admin && agent.publish_problems.length > 0 && !dirty && (
          <div style={{ marginTop: 8, fontSize: 12, color: MUTED }}>
            Before publishing: {agent.publish_problems.join(' ')}
          </div>
        )}
      </div>

      <div className="flex min-h-0 flex-1" style={{ padding: 16, gap: 16 }}>
        <nav aria-label="Agent sections" className="shrink-0 overflow-y-auto bg-white"
          style={{ width: 210, border: `1px solid ${LINE}`, borderRadius: 8, padding: 8 }}>
          {BUILDER_SECTIONS.map((s) => (
            <button key={s.key} type="button" onClick={() => setSection(s.key)}
              aria-current={section === s.key ? 'page' : undefined}
              className="block w-full text-left"
              style={{ padding: '8px 10px', borderRadius: 6, fontSize: 13, marginBottom: 2,
                fontWeight: section === s.key ? 600 : 400,
                color: section === s.key ? 'rgb(0,78,235)' : MUTED,
                backgroundColor: section === s.key ? PRIMARY_TINT : 'transparent' }}>
              {s.label}
            </button>
          ))}
        </nav>

        <div className="min-w-0 flex-1 overflow-y-auto bg-white"
          style={{ border: `1px solid ${LINE}`, borderRadius: 8, padding: '18px 22px' }}>
          <SectionBody section={section} agent={agent} admin={admin} draft={draft} set={set}
            name={name} setName={setName} description={description} setDescription={setDescription}
            folderId={folderId} setFolderId={setFolderId} />
        </div>

        {admin && <TryItPanel agent={agent} draft={draft} />}
      </div>

      {confirmAuto && (
        <Confirm title="Switch to Auto-pilot?" confirmLabel="Switch to Auto-pilot" busy={mode.isPending}
          body={<>“{agent.name}” will carry out its allowed actions without asking — texting customers,
            booking, moving stages — using its published version (v{agent.published_version ?? '—'}).
            Suggest mode asks staff to approve each change instead.</>}
          onCancel={() => setConfirmAuto(false)} onConfirm={() => mode.mutate({ to: 'auto', confirm: true })} />
      )}
    </div>
  )
}

// ------------------------------------------------------------------ sections

type SectionProps = {
  section: BuilderSection; agent: AiAgentDetail; admin: boolean; draft: Draft
  set: (p: Partial<Draft>) => void
  name: string; setName: (v: string) => void
  description: string; setDescription: (v: string) => void
  folderId: number | null; setFolderId: (v: number | null) => void
}

function ReadText({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 14, color: TEXT, marginTop: 6, whiteSpace: 'pre-wrap' }}>{children || '—'}</div>
}

function Help({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, color: FAINT, marginTop: 4, lineHeight: 1.5 }}>{children}</div>
}

function SectionBody(p: SectionProps) {
  switch (p.section) {
    case 'basics': return <Basics {...p} />
    case 'persona': return <Persona {...p} />
    case 'rules': return <Rules {...p} />
    case 'knowledge': return <Knowledge {...p} />
    case 'actions': return <Actions {...p} />
    case 'triggers': return <Triggers {...p} />
    case 'schedule': return <Schedule {...p} />
    case 'connection': return <Connection {...p} />
    case 'escalation': return <Escalation {...p} />
    case 'advanced': return <Advanced {...p} />
    case 'prompt': return <CompiledPrompt {...p} />
    case 'versions': return <Versions {...p} />
  }
}

function Basics({ admin, name, setName, description, setDescription, folderId, setFolderId }: SectionProps) {
  const folders = useQuery({ queryKey: ['ai-folders'], queryFn: aiFolders })
  const folderName = (folders.data ?? []).find((f) => f.id === folderId)?.name
  return (
    <>
      <SectionTitle>Basics</SectionTitle>
      <Field label="Agent name" required>
        {admin ? <input value={name} onChange={(e) => setName(e.target.value)} style={INPUT} aria-label="Agent name" />
          : <ReadText>{name}</ReadText>}
      </Field>
      <Field label="Description">
        {admin ? <textarea value={description} onChange={(e) => setDescription(e.target.value)} style={textarea}
          aria-label="Description" /> : <ReadText>{description}</ReadText>}
      </Field>
      <Field label="Folder">
        {admin ? (
          <select value={folderId ?? ''} onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : null)}
            style={INPUT} aria-label="Folder">
            <option value="">No folder</option>
            {(folders.data ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        ) : <ReadText>{folderName ?? 'No folder'}</ReadText>}
      </Field>
      <div style={{ marginTop: 14 }}>
        <div style={LABEL}>Channel</div>
        <ReadText>Text / Chat</ReadText>
      </div>
    </>
  )
}

function ListEditor({ label, items, admin, placeholder, onChange }: {
  label: string; items: string[]; admin: boolean; placeholder: string; onChange: (l: string[]) => void
}) {
  return (
    <div style={{ marginTop: 16 }}>
      <div style={LABEL}>{label}</div>
      {!admin && (items.length ? (
        <ol style={{ margin: '6px 0 0', paddingLeft: 20, fontSize: 14, color: TEXT }}>
          {items.map((g, i) => <li key={i} style={{ marginTop: 4 }}>{g}</li>)}
        </ol>
      ) : <ReadText>—</ReadText>)}
      {admin && items.map((g, i) => (
        <div key={i} className="flex items-center gap-8" style={{ marginTop: 8 }}>
          <span style={{ width: 18, fontSize: 13, color: FAINT, textAlign: 'right' }}>{i + 1}.</span>
          <input value={g} onChange={(e) => onChange(setItem(items, i, e.target.value))}
            placeholder={placeholder} aria-label={`${label} ${i + 1}`}
            style={{ ...INPUT, marginTop: 0 }} />
          <button type="button" aria-label="Move up" disabled={i === 0} onClick={() => onChange(moveItem(items, i, -1))}
            style={{ padding: 4, color: i === 0 ? 'rgb(208,213,221)' : MUTED }}>↑</button>
          <button type="button" aria-label="Move down" disabled={i === items.length - 1}
            onClick={() => onChange(moveItem(items, i, 1))}
            style={{ padding: 4, color: i === items.length - 1 ? 'rgb(208,213,221)' : MUTED }}>↓</button>
          <button type="button" aria-label={`Remove ${label} ${i + 1}`} onClick={() => onChange(removeItem(items, i))}
            style={{ padding: 4 }}>
            <IconTrashOutline size={16} color={MUTED} />
          </button>
        </div>
      ))}
      {admin && (
        <button type="button" onClick={() => onChange(addItem(items))}
          className="flex items-center gap-4" style={{ ...textButton, marginTop: 10 }}>
          <IconPlus size={14} color={BLUE} /> Add
        </button>
      )}
    </div>
  )
}

function Persona({ admin, draft, set }: SectionProps) {
  return (
    <>
      <SectionTitle>Persona & goals</SectionTitle>
      <Help>Who the agent is and what it is trying to achieve. Customer details come from the CRM, not from here.</Help>
      <Field label="Role / persona">
        {admin ? (
          <textarea value={draft.persona} onChange={(e) => set({ persona: e.target.value })}
            style={{ ...textarea, minHeight: 120 }} aria-label="Role / persona"
            placeholder="You are the friendly office assistant for Dream Team Roofing…" />
        ) : <ReadText>{draft.persona}</ReadText>}
      </Field>
      <ListEditor label="Goals" items={draft.goals} admin={admin} placeholder="e.g. Book an inspection"
        onChange={(goals) => set({ goals })} />
    </>
  )
}

function Rules({ admin, draft, set }: SectionProps) {
  return (
    <>
      <SectionTitle>Rules</SectionTitle>
      <Help>Plain instructions. The safety rules (customer text is data, never invent prices) are always added.</Help>
      <ListEditor label="Do" items={draft.rules_do} admin={admin} placeholder="e.g. Confirm the service address"
        onChange={(rules_do) => set({ rules_do })} />
      <ListEditor label="Don't" items={draft.rules_dont} admin={admin} placeholder="e.g. Quote a price"
        onChange={(rules_dont) => set({ rules_dont })} />
    </>
  )
}

function CheckRow({ checked, onChange, label, help, admin }: {
  checked: boolean; onChange: () => void; label: string; help?: string; admin: boolean
}) {
  if (!admin) {
    return checked ? (
      <div style={{ padding: '8px 0', borderBottom: `1px solid ${LINE}` }}>
        <div style={{ fontSize: 14, color: TEXT }}>{label}</div>
        {help && <div style={{ fontSize: 12, color: FAINT, marginTop: 2 }}>{help}</div>}
      </div>
    ) : null
  }
  return (
    <label className="flex items-start gap-12" style={{ padding: '8px 0', borderBottom: `1px solid ${LINE}`, cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={onChange}
        style={{ marginTop: 3, width: 16, height: 16, accentColor: BLUE }} />
      <span>
        <span style={{ fontSize: 14, color: TEXT }}>{label}</span>
        {help && <span className="block" style={{ fontSize: 12, color: FAINT, marginTop: 2 }}>{help}</span>}
      </span>
    </label>
  )
}

function Knowledge({ admin, draft, set }: SectionProps) {
  const kbs = useQuery({ queryKey: ['ai-kbs'], queryFn: aiKbs })
  const ids = (kbs.data ?? []).map((k) => k.id)
  return (
    <>
      <SectionTitle>Knowledge</SectionTitle>
      <Help>The agent's search_knowledge tool only searches the knowledge bases attached here.</Help>
      <div style={{ marginTop: 10 }}>
        {(kbs.data ?? []).map((kb) => (
          <CheckRow key={kb.id} admin={admin} checked={draft.knowledge_base_ids.includes(kb.id)}
            onChange={() => set({ knowledge_base_ids: toggle(draft.knowledge_base_ids, kb.id, ids) })}
            label={kb.name} help={`${kb.faqs} FAQs · ${kb.articles} articles · ${kb.files} files`} />
        ))}
        {kbs.isSuccess && kbs.data.length === 0 && <Help>No knowledge bases yet — add one on the Knowledge Base tab.</Help>}
        {!admin && draft.knowledge_base_ids.length === 0 && <ReadText>None attached</ReadText>}
      </div>
    </>
  )
}

function Actions({ admin, draft, set }: SectionProps) {
  const cat = useQuery({ queryKey: ['ai-catalogue'], queryFn: aiCatalogue })
  // Voice-only actions (transfer call, end call) are phase 3 and never offered to a Text agent.
  const offered = (cat.data?.actions ?? []).filter((a) => a.channels.includes('text'))
  const order = offered.map((a) => a.name)
  return (
    <>
      <SectionTitle>Actions</SectionTitle>
      <Help>Each action is a tool the agent may use. Every change is checked by the CRM: it can only touch this
        run's own customer and opportunity. In Suggest mode every change waits for staff approval.</Help>
      <div style={{ marginTop: 10 }}>
        {offered.map((a) => (
          <CheckRow key={a.name} admin={admin} checked={draft.actions.includes(a.name)}
            onChange={() => set({ actions: toggle(draft.actions, a.name, order) })}
            label={a.label + (a.kind === 'write' ? ' — changes the CRM or contacts the customer' : '')}
            help={a.description} />
        ))}
      </div>
    </>
  )
}

function Triggers({ admin, draft, set }: SectionProps) {
  const cat = useQuery({ queryKey: ['ai-catalogue'], queryFn: aiCatalogue })
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  const [adding, setAdding] = useState('')
  const stageName = (id: number) => {
    for (const p of pipelines.data ?? []) {
      const s = p.stages.find((x) => x.id === id)
      if (s) return `${p.name} › ${s.name}`
    }
    return undefined
  }
  const update = (i: number, patch: Partial<Trigger>) =>
    set({ triggers: draft.triggers.map((t, j) => (j === i ? { ...t, ...patch } : t)) })
  return (
    <>
      <SectionTitle>Triggers</SectionTitle>
      <Help>When the agent runs. A number no contact holds never triggers an agent, and an Off agent never runs.</Help>
      {draft.triggers.length === 0 && <ReadText>No triggers</ReadText>}
      {draft.triggers.map((t, i) => (
        <div key={i} style={{ marginTop: 10, padding: 12, border: `1px solid ${LINE}`, borderRadius: 8 }}>
          <div className="flex items-center">
            <div style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>
              {admin ? (TRIGGER_LABEL[t.type] ?? t.type) : triggerSummary(t, stageName)}
            </div>
            {admin && (
              <button type="button" className="ml-auto" aria-label={`Remove trigger ${i + 1}`}
                onClick={() => set({ triggers: removeItem(draft.triggers, i) })} style={{ padding: 4 }}>
                <IconClose size={16} color={MUTED} />
              </button>
            )}
          </div>
          {admin && t.type === 'inbound_text' && (
            <Field label="Minutes without a reply">
              <input type="number" min={0} value={t.minutes ?? 10} aria-label="Minutes without a reply"
                onChange={(e) => update(i, { minutes: Math.max(0, Number(e.target.value) || 0) })}
                style={{ ...INPUT, width: 140 }} />
            </Field>
          )}
          {admin && t.type === 'stage_entered' && (
            <div className="flex gap-12">
              <div className="flex-1">
                <Field label="Pipeline">
                  <select value={t.pipeline_id ?? ''} aria-label="Pipeline" style={INPUT}
                    onChange={(e) => update(i, { pipeline_id: e.target.value ? Number(e.target.value) : undefined, stage_id: undefined })}>
                    <option value="">Choose a pipeline</option>
                    {(pipelines.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </Field>
              </div>
              <div className="flex-1">
                <Field label="Stage">
                  <select value={t.stage_id ?? ''} aria-label="Stage" style={INPUT}
                    onChange={(e) => update(i, { stage_id: e.target.value ? Number(e.target.value) : undefined })}>
                    <option value="">Choose a stage</option>
                    {((pipelines.data ?? []).find((p) => p.id === t.pipeline_id)?.stages ?? []).map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </Field>
              </div>
            </div>
          )}
        </div>
      ))}
      {admin && (
        <div className="flex items-end gap-8" style={{ marginTop: 14 }}>
          <div style={{ width: 280 }}>
            <select value={adding} onChange={(e) => setAdding(e.target.value)} style={{ ...INPUT, marginTop: 0 }}
              aria-label="Trigger to add">
              <option value="">Choose a trigger…</option>
              {(cat.data?.triggers ?? []).map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
            </select>
          </div>
          <button type="button" disabled={!adding}
            onClick={() => { set({ triggers: [...draft.triggers, newTrigger(adding)] }); setAdding('') }}
            style={{ ...smallButton, height: 36, opacity: adding ? 1 : 0.5 }}>
            <IconPlus size={14} color={BODY} /> Add trigger
          </button>
        </div>
      )}
    </>
  )
}

function Schedule({ admin, draft, set }: SectionProps) {
  const s = draft.schedule
  const setS = (patch: Partial<Draft['schedule']>) => set({ schedule: { ...s, ...patch } })
  if (!admin) {
    return (
      <>
        <SectionTitle>Schedule & behaviour</SectionTitle>
        <Field label="When it may act"><ReadText>{scheduleSummary(s)}</ReadText></Field>
        <Field label="Sleep when staff reply"><ReadText>{draft.sleep_on_staff_reply ? 'On' : 'Off'}</ReadText></Field>
        <Field label="Wait before acting"><ReadText>{draft.wait_minutes} min</ReadText></Field>
        <Field label="Max messages per conversation"><ReadText>{draft.max_messages}</ReadText></Field>
      </>
    )
  }
  return (
    <>
      <SectionTitle>Schedule & behaviour</SectionTitle>
      <div className="flex items-center gap-12" style={{ marginTop: 12, padding: 12, border: `1px solid ${LINE}`, borderRadius: 8 }}>
        <div className="flex-1">
          <div style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>Only act during set hours</div>
          <div style={{ fontSize: 12, color: FAINT, marginTop: 2 }}>
            Eastern time (America/New_York). Outside these hours the agent does not run, and the log says why.
          </div>
        </div>
        <Switch checked={s.enabled} onChange={(v) => setS({ enabled: v })} label="Only act during set hours" />
      </div>
      {s.enabled && (
        <>
          <div className="flex flex-wrap gap-8" style={{ marginTop: 12 }} role="group" aria-label="Days">
            {DAYS.map((d) => (
              <button key={d} type="button" aria-pressed={s.days.includes(d)}
                onClick={() => setS({ days: toggle(s.days, d, [...DAYS]) })}
                style={{ height: 30, width: 48, borderRadius: 6, fontSize: 13, fontWeight: 500,
                  border: `1px solid ${s.days.includes(d) ? BLUE : BORDER}`,
                  color: s.days.includes(d) ? 'rgb(0,78,235)' : BODY,
                  backgroundColor: s.days.includes(d) ? PRIMARY_TINT : '#fff' }}>
                {dayLabel(d)}
              </button>
            ))}
          </div>
          <div className="flex gap-12">
            <Field label="From"><input type="time" value={s.start} onChange={(e) => setS({ start: e.target.value })}
              style={{ ...INPUT, width: 140 }} aria-label="From" /></Field>
            <Field label="Until"><input type="time" value={s.end} onChange={(e) => setS({ end: e.target.value })}
              style={{ ...INPUT, width: 140 }} aria-label="Until" /></Field>
          </div>
          <Help>{scheduleSummary(s)}</Help>
        </>
      )}
      <div className="flex items-center gap-12" style={{ marginTop: 12, padding: 12, border: `1px solid ${LINE}`, borderRadius: 8 }}>
        <div className="flex-1">
          <div style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>Sleep when staff reply</div>
          <div style={{ fontSize: 12, color: FAINT, marginTop: 2 }}>
            When a person texts or calls the customer, the agent stops on that conversation and its queued follow-up is cancelled.
          </div>
        </div>
        <Switch checked={draft.sleep_on_staff_reply} onChange={(v) => set({ sleep_on_staff_reply: v })} label="Sleep when staff reply" />
      </div>
      <div className="flex gap-12">
        <Field label="Wait before acting (minutes)">
          <input type="number" min={0} value={draft.wait_minutes} aria-label="Wait before acting"
            onChange={(e) => set({ wait_minutes: Math.max(0, Number(e.target.value) || 0) })} style={{ ...INPUT, width: 160 }} />
        </Field>
        <Field label="Max messages per conversation">
          <input type="number" min={0} max={50} value={draft.max_messages} aria-label="Max messages per conversation"
            onChange={(e) => set({ max_messages: Math.max(0, Number(e.target.value) || 0) })} style={{ ...INPUT, width: 160 }} />
        </Field>
      </div>
    </>
  )
}

function Connection({ admin, draft, set }: SectionProps) {
  const conns = useQuery({ queryKey: ['ai-connection-names'], queryFn: aiConnectionNames })
  const current = (conns.data ?? []).find((c) => c.id === draft.connection_id)
  return (
    <>
      <SectionTitle>AI connection</SectionTitle>
      <Help>Which provider account and model this agent uses. Connections are managed in Settings → AI Connections.</Help>
      <Field label="Connection" required>
        {admin ? (
          <select value={draft.connection_id ?? ''} aria-label="Connection" style={INPUT}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null
              const c = (conns.data ?? []).find((x) => x.id === id)
              set({ connection_id: id, model: draft.model || c?.default_model || '' })
            }}>
            <option value="">Choose a connection</option>
            {(conns.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        ) : <ReadText>{current?.name ?? 'None'}</ReadText>}
      </Field>
      {admin && conns.isSuccess && conns.data.length === 0 && (
        <Help>No AI connections yet. An admin adds one in Settings → AI Connections.</Help>
      )}
      <Field label="Model" required hint={current ? `The connection's default is ${current.default_model}.` : undefined}>
        {admin ? (
          <input value={draft.model} onChange={(e) => set({ model: e.target.value })} style={INPUT}
            aria-label="Model" placeholder={current?.default_model ?? 'claude-sonnet-5'} />
        ) : <ReadText>{draft.model}</ReadText>}
      </Field>
    </>
  )
}

function Escalation({ admin, draft, set }: SectionProps) {
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  // The line an escalation text leaves on, from the server (2026-09-16).
  const ourNumber = useOurLine()
  const staff = (users.data ?? []).filter((u) => (u.role === 'ADMIN' || u.role === 'DISPATCHER')
    && (u as { is_active?: boolean }).is_active !== false)
  const ids = staff.map((u) => u.id)
  return (
    <>
      <SectionTitle>Escalation</SectionTitle>
      <Help>Who gets an urgent task and an alert when the agent hands a conversation to a person. An emergency also
        texts the on-call phone set in Settings → AI Connections, from {formatPhone(ourNumber)} through the phone system.</Help>
      <div style={{ marginTop: 10 }}>
        {staff.map((u) => (
          <CheckRow key={u.id} admin={admin} checked={draft.escalation_user_ids.includes(u.id)}
            onChange={() => set({ escalation_user_ids: toggle(draft.escalation_user_ids, u.id, ids) })}
            label={u.name} help={u.role === 'ADMIN' ? 'Admin' : 'Dispatcher'} />
        ))}
        {!admin && draft.escalation_user_ids.length === 0 && <ReadText>Nobody</ReadText>}
      </div>
    </>
  )
}

function Advanced({ admin, draft, set }: SectionProps) {
  return (
    <>
      <SectionTitle>Advanced</SectionTitle>
      <Field label="Extra instructions" hint="Added at the end of the compiled prompt, word for word.">
        {admin ? (
          <textarea value={draft.extra_instructions} onChange={(e) => set({ extra_instructions: e.target.value })}
            style={{ ...textarea, minHeight: 160 }} aria-label="Extra instructions" />
        ) : <ReadText>{draft.extra_instructions}</ReadText>}
      </Field>
    </>
  )
}

function CompiledPrompt({ agent, draft, name }: SectionProps) {
  const body = useMemo(() => draftForSave(draft), [draft])
  const preview = useQuery({
    queryKey: ['ai-prompt', agent.id, JSON.stringify(body), name],
    queryFn: () => previewAiPrompt(agent.id, body, name),
  })
  return (
    <>
      <SectionTitle>Compiled prompt</SectionTitle>
      <Help>Read-only. Assembled from the sections above in a fixed order, with no timestamps, so the provider can
        cache it. Customer details are sent separately with each run.</Help>
      {preview.isError && <div role="alert" style={{ marginTop: 10, fontSize: 13, color: 'rgb(180,35,24)' }}>
        {(preview.error as Error).message}</div>}
      {preview.data && (
        <>
          <pre aria-label="Compiled prompt" style={{ marginTop: 12, padding: 14, backgroundColor: PAGE_BG,
            border: `1px solid ${LINE}`, borderRadius: 8, fontSize: 12, lineHeight: 1.55, color: TEXT,
            whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>
            {preview.data.compiled_prompt}
          </pre>
          {preview.data.publish_problems.length > 0 && (
            <Help>Not publishable yet: {preview.data.publish_problems.join(' ')}</Help>
          )}
        </>
      )}
    </>
  )
}

function Versions({ agent }: SectionProps) {
  return (
    <>
      <SectionTitle>Versions</SectionTitle>
      <Help>Publishing freezes the draft as a new version. Every run records the version it used.</Help>
      {agent.versions.length === 0 && <ReadText>Not published yet</ReadText>}
      {agent.versions.map((v) => (
        <div key={v.id} className="flex items-center gap-12" style={{ padding: '10px 0', borderBottom: `1px solid ${LINE}` }}>
          <div style={{ fontSize: 14, fontWeight: 500, color: TEXT, width: 40 }}>v{v.version}</div>
          <div style={{ fontSize: 13, color: MUTED }}>
            {stamp(v.published_at)}{v.published_by ? ` · ${v.published_by}` : ''}
          </div>
          {v.current && <Chip text="Current" fg="rgb(2,122,72)" bg="rgb(236,253,243)" />}
        </div>
      ))}
    </>
  )
}

// ------------------------------------------------------------------ Try it

type ChatTurn = { role: 'user' | 'assistant'; content: string; would?: AiWould[]; meta?: string; failed?: boolean }

function TryItPanel({ agent, draft }: { agent: AiAgentDetail; draft: Draft }) {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [text, setText] = useState('')
  const [contact, setContact] = useState<{ id: number; name: string } | null>(null)
  const [opportunityId, setOpportunityId] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)

  const found = useQuery({
    queryKey: ['ai-try-contacts', search], enabled: search.trim().length >= 2 && !contact,
    queryFn: () => listContacts({ page: 1, page_size: 6, q: search.trim() }),
  })
  const contactDetail = useQuery({
    queryKey: ['contact', contact?.id], enabled: !!contact, queryFn: () => getContact(contact!.id),
  })
  useEffect(() => { setOpportunityId(null) }, [contact?.id])

  const send = useMutation({
    mutationFn: (history: ChatTurn[]) => tryAiAgent(agent.id, {
      messages: history.map((t) => ({ role: t.role, content: t.content })),
      contact_id: contact?.id ?? null, opportunity_id: opportunityId, draft: draftForSave(draft),
    }),
    onSuccess: (r) => {
      setError(null)
      setTurns((prev) => [...prev, {
        role: 'assistant',
        content: r.reply || (r.outcome === 'completed' ? '(no reply)' : `${outcomeLabel(r.outcome)}: ${r.reason ?? ''}`),
        would: r.would, failed: !r.reply,
        meta: `${formatTokens(r.tokens)} · ${formatCost(r.cost)} · ${formatLatency(r.latency_ms)}`,
      }])
    },
    onError: (e: Error, history) => { setError(e.message); setTurns(history.slice(0, -1)) },
  })

  const submit = () => {
    const content = text.trim()
    if (!content || send.isPending) return
    // Only real exchanges go back to the model: a failed turn is not part of the chat.
    const history = [...turns.filter((t) => !t.failed), { role: 'user' as const, content }]
    setTurns([...turns, { role: 'user', content }])
    setText('')
    send.mutate(history)
  }

  return (
    <aside aria-label="Try it" className="flex shrink-0 flex-col bg-white"
      style={{ width: 380, border: `1px solid ${LINE}`, borderRadius: 8 }}>
      <div className="flex items-center" style={{ padding: '12px 14px', borderBottom: `1px solid ${LINE}` }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 600, color: INK }}>Try it</div>
          <div style={{ fontSize: 12, color: FAINT, marginTop: 2 }}>
            Uses this draft. Reads run for real; no change is ever made.
          </div>
        </div>
        {turns.length > 0 && (
          <button type="button" className="ml-auto" onClick={() => { setTurns([]); setError(null) }} style={textButton}>
            Reset
          </button>
        )}
      </div>

      <div style={{ padding: '10px 14px', borderBottom: `1px solid ${LINE}` }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: BODY }}>Test as</div>
        {contact ? (
          <div style={{ marginTop: 6 }}>
            <div className="flex items-center gap-8">
              <span style={{ fontSize: 13, color: TEXT }}>{contact.name}</span>
              <button type="button" aria-label="Clear test contact" onClick={() => { setContact(null); setSearch('') }}
                style={{ padding: 2 }}>
                <IconClose size={14} color={MUTED} />
              </button>
            </div>
            {(contactDetail.data?.opportunities ?? []).length > 0 && (
              <select value={opportunityId ?? ''} aria-label="Test opportunity"
                onChange={(e) => setOpportunityId(e.target.value ? Number(e.target.value) : null)}
                style={{ ...INPUT, height: 32, fontSize: 13, marginTop: 6 }}>
                <option value="">No opportunity</option>
                {(contactDetail.data?.opportunities ?? []).map((o) => (
                  <option key={o.id} value={o.id}>{o.title}</option>
                ))}
              </select>
            )}
          </div>
        ) : (
          <div className="relative">
            <input value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Test as contact"
              placeholder="A customer, for real context (optional)"
              style={{ ...INPUT, height: 32, fontSize: 13, marginTop: 6 }} />
            {(found.data?.items ?? []).length > 0 && (
              <div role="listbox" className="absolute left-0 right-0 bg-white" style={{ top: 42, zIndex: 10,
                border: `1px solid ${LINE}`, borderRadius: 6, boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)' }}>
                {(found.data?.items ?? []).map((c) => (
                  <button key={c.id} type="button" role="option" aria-selected={false}
                    onClick={() => setContact({ id: c.id, name: c.name || c.phone_display || `Contact #${c.id}` })}
                    className="block w-full text-left hover:bg-[rgb(249,250,251)]"
                    style={{ padding: '7px 10px', fontSize: 13, color: TEXT }}>
                    {c.name || '(no name)'}
                    <span style={{ color: FAINT, marginLeft: 6 }}>{c.phone_display ?? c.email ?? ''}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto" style={{ padding: 14, backgroundColor: PAGE_BG }}>
        {turns.length === 0 && (
          <div style={{ fontSize: 13, color: FAINT, textAlign: 'center', marginTop: 24 }}>
            Write a message as the customer would.
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`flex flex-col ${t.role === 'user' ? 'items-end' : 'items-start'}`} style={{ marginBottom: 10 }}>
            <div data-role={t.role} style={{ maxWidth: '88%', padding: '8px 12px', borderRadius: 12, fontSize: 13,
              lineHeight: 1.45, whiteSpace: 'pre-wrap',
              color: t.role === 'user' ? '#fff' : TEXT,
              backgroundColor: t.role === 'user' ? BLUE : '#fff',
              border: t.role === 'user' ? 'none' : `1px solid ${LINE}` }}>
              {t.content}
            </div>
            {(t.would ?? []).map((w, j) => (
              <div key={j} data-would className="flex items-start gap-8" style={{ maxWidth: '88%', marginTop: 6,
                padding: '8px 10px', borderRadius: 8, fontSize: 12, color: 'rgb(181,71,8)',
                backgroundColor: 'rgb(255,250,235)', border: '1px solid rgb(254,223,137)' }}>
                <IconSparkle size={14} color="rgb(181,71,8)" />
                <span>{wouldText(w)}</span>
              </div>
            ))}
            {t.meta && <div style={{ fontSize: 11, color: FAINT, marginTop: 4 }}>{t.meta}</div>}
          </div>
        ))}
        {send.isPending && <div style={{ fontSize: 12, color: FAINT }}>The agent is thinking…</div>}
        {error && <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)', marginTop: 6 }}>{error}</div>}
      </div>

      <div className="flex items-end gap-8" style={{ padding: 10, borderTop: `1px solid ${LINE}` }}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} aria-label="Message as the customer"
          placeholder="Type a customer message"
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
          style={{ ...textarea, marginTop: 0, minHeight: 40, height: 40, fontSize: 13 }} />
        <button type="button" onClick={submit} disabled={!text.trim() || send.isPending}
          style={{ ...primarySmall, height: 40, opacity: !text.trim() || send.isPending ? 0.5 : 1 }}>
          Send
        </button>
      </div>
      <div style={{ padding: '0 14px 8px', fontSize: 11, color: FAINT }}>
        Each test is logged in Agent Logs with its full transcript.
      </div>
    </aside>
  )
}
