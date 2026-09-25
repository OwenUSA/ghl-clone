import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  aiAgents, aiFolders, aiTemplates, createAiAgent, createAiFolder, deleteAiAgent, deleteAiFolder,
  duplicateAiAgent, renameAiFolder, saveAiTemplate, setAiAgentMode, updateAiAgent,
  type AiAgentRow,
} from '../../lib/api'
import type { Me } from '../../lib/auth'
import {
  answeringCell, channelLabel, MODE_TONE, isAiAdmin, modeLabel, OUTCOME_TONE, outcomeLabel, stamp, TRIGGER_LABEL,
} from '../../lib/aiAgents'
import {
  IconDuplicate, IconPencil, IconPlus, IconSearchSmall, IconTrashOutline,
} from '../PipelineIcons'
import { IconSparkle } from '../Icon'
import {
  BLUE, Chip, Confirm, Empty, Field, INK, INPUT, LINE, Modal, MUTED, Notice, PageHeader,
  primarySmall, RowMenu, smallButton, TableCard, Td, textarea, Th,
} from './aiUi'

/**
 * Agents: folders on the left of the toolbar ("All agents" + each folder), a search box, and
 * the Pipelines list's table (refs/opps/02): Name · Channel · Mode · Published · Last run ·
 * Actions ⋮. An ADMIN creates, edits, duplicates, files, saves as template and deletes; a
 * dispatcher opens an agent read-only and can switch it off — nothing else is drawn for them.
 */
export function AgentsTab({ user, onOpen, templateToUse, onTemplateUsed }: {
  user: Me; onOpen: (id: number) => void
  templateToUse: number | null; onTemplateUsed: () => void
}) {
  const qc = useQueryClient()
  const admin = isAiAdmin(user)
  const agents = useQuery({ queryKey: ['ai-agents'], queryFn: aiAgents })
  const folders = useQuery({ queryKey: ['ai-folders'], queryFn: aiFolders })
  const [folder, setFolder] = useState<number | null>(null)
  const [q, setQ] = useState('')
  const [menu, setMenu] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [creating, setCreating] = useState<{ template: number | null } | null>(null)
  const [moving, setMoving] = useState<AiAgentRow | null>(null)
  const [templating, setTemplating] = useState<AiAgentRow | null>(null)
  const [deleting, setDeleting] = useState<AiAgentRow | null>(null)
  const [folderDialog, setFolderDialog] = useState<{ id: number | null; name: string } | null>(null)

  useEffect(() => {
    if (templateToUse != null && admin) {
      setCreating({ template: templateToUse })
      onTemplateUsed()
    }
  }, [templateToUse, admin, onTemplateUsed])

  const refresh = () => {
    for (const k of ['ai-agents', 'ai-folders', 'ai-templates']) qc.invalidateQueries({ queryKey: [k] })
  }
  const failed = (e: Error) => { setNotice(null); setError(e.message) }

  const duplicate = useMutation({
    mutationFn: duplicateAiAgent,
    onSuccess: (a) => { refresh(); setNotice(`Created “${a.name}”. It is Off.`) }, onError: failed,
  })
  const switchOff = useMutation({
    mutationFn: (id: number) => setAiAgentMode(id, 'off'),
    onSuccess: (a) => { refresh(); setNotice(`“${a.name}” is Off.`) }, onError: failed,
  })
  const remove = useMutation({
    mutationFn: deleteAiAgent,
    onSuccess: () => { setDeleting(null); refresh(); setNotice('Agent deleted. Its logs are kept.') },
    onError: (e: Error) => { setDeleting(null); failed(e) },
  })
  const removeFolder = useMutation({
    mutationFn: deleteAiFolder,
    onSuccess: (r) => { setFolder(null); refresh(); setNotice(`Folder deleted; ${r.agents_moved_out} agent(s) moved out.`) },
    onError: failed,
  })

  const all = agents.data ?? []
  const needle = q.trim().toLowerCase()
  const rows = all.filter((a) => (folder == null || a.folder_id === folder)
    && (!needle || a.name.toLowerCase().includes(needle) || (a.description ?? '').toLowerCase().includes(needle)))
  const currentFolder = (folders.data ?? []).find((f) => f.id === folder) ?? null

  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 16px' }}>
      <PageHeader title="AI Agents"
        subtitle="Agents answer and follow up with customers by text, using your knowledge bases and the CRM. Every agent starts Off.">
        {admin && (
          <button type="button" onClick={() => setCreating({ template: null })} style={primarySmall}>
            <IconPlus size={14} color="#fff" /> Create agent
          </button>
        )}
      </PageHeader>
      <Notice error={error} notice={notice} onDismiss={() => { setError(null); setNotice(null) }} />

      <TableCard toolbar={
        <>
          <div role="tablist" aria-label="Folders" className="flex flex-wrap items-center gap-4">
            <FolderPill label={`All agents (${all.length})`} active={folder == null} onClick={() => setFolder(null)} />
            {(folders.data ?? []).map((f) => (
              <FolderPill key={f.id} label={`${f.name} (${f.agents})`} active={folder === f.id}
                onClick={() => setFolder(f.id)} />
            ))}
            {admin && (
              <button type="button" onClick={() => setFolderDialog({ id: null, name: '' })}
                style={{ fontSize: 13, color: BLUE, padding: '0 6px', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <IconPlus size={12} color={BLUE} /> New folder
              </button>
            )}
            {admin && currentFolder && (
              <>
                <button type="button" style={{ fontSize: 13, color: BLUE, padding: '0 6px' }}
                  onClick={() => setFolderDialog({ id: currentFolder.id, name: currentFolder.name })}>
                  Rename folder
                </button>
                <button type="button" style={{ fontSize: 13, color: 'rgb(180,35,24)', padding: '0 6px' }}
                  onClick={() => removeFolder.mutate(currentFolder.id)}>
                  Delete folder
                </button>
              </>
            )}
          </div>
          <label className="ml-auto flex items-center gap-4" style={{ height: 26, width: 180, padding: '0 6px',
            border: '1px solid rgb(208,213,221)', borderRadius: 4 }}>
            <IconSearchSmall size={12} color={MUTED} />
            <input value={q} placeholder="Search agents" aria-label="Search agents"
              onChange={(e) => setQ(e.target.value)}
              style={{ width: '100%', fontSize: 12, outline: 'none', color: INK }} />
          </label>
        </>
      }>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 1090 }}>
          <thead>
            <tr>
              <Th>Agent name</Th>
              <Th width={120}>Channel</Th>
              <Th width={190}>Answering calls</Th>
              <Th width={120}>Mode</Th>
              <Th width={150}>Published</Th>
              <Th width={200}>Triggers</Th>
              <Th width={170}>Last run</Th>
              <Th width={80} align="center">Actions</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id} className="hover:bg-[rgb(249,250,251)]">
                <Td>
                  <button type="button" onClick={() => onOpen(a.id)} className="text-left"
                    style={{ fontSize: 13, color: 'rgb(0,78,235)', fontWeight: 500 }}>
                    {a.name}
                  </button>
                  {a.description && (
                    <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{a.description}</div>
                  )}
                  {a.folder_name && folder == null && (
                    <div style={{ fontSize: 12, color: 'rgb(152,162,179)', marginTop: 2 }}>{a.folder_name}</div>
                  )}
                </Td>
                <Td>
                  {channelLabel(a.channel)}
                </Td>
                <Td>
                  {(() => {
                    const cell = answeringCell(a)
                    return cell ? <Chip text={cell.text} fg={cell.fg} bg={cell.bg} />
                      : <span style={{ fontSize: 12, color: MUTED }}>—</span>
                  })()}
                </Td>
                <Td><Chip text={modeLabel(a.mode)} {...(MODE_TONE[a.mode] ?? MODE_TONE.off)} /></Td>
                <Td>
                  {a.published_version != null ? `v${a.published_version}` : 'Draft'}
                  {a.published_version != null && a.has_unpublished_changes && (
                    <div style={{ fontSize: 12, color: 'rgb(181,71,8)', marginTop: 2 }}>Unpublished changes</div>
                  )}
                </Td>
                <Td>
                  <span style={{ fontSize: 12, color: MUTED }}>
                    {a.triggers.length ? a.triggers.map((t) => TRIGGER_LABEL[t] ?? t).join(', ') : '—'}
                  </span>
                </Td>
                <Td>
                  {a.last_run_at ? (
                    <>
                      <div>{stamp(a.last_run_at)}</div>
                      {a.last_outcome && (
                        <div style={{ marginTop: 2 }}>
                          <Chip text={outcomeLabel(a.last_outcome)} {...(OUTCOME_TONE[a.last_outcome] ?? OUTCOME_TONE.queued)} />
                        </div>
                      )}
                    </>
                  ) : '—'}
                </Td>
                <Td align="center">
                  <RowMenu label={`Actions for ${a.name}`} open={menu === a.id}
                    onToggle={() => setMenu((m) => (m === a.id ? null : a.id))} onClose={() => setMenu(null)}
                    items={admin ? [
                      { label: 'Edit', icon: <IconPencil size={16} color={INK} />, run: () => onOpen(a.id) },
                      { label: 'Duplicate', icon: <IconDuplicate size={16} color={INK} />, run: () => duplicate.mutate(a.id) },
                      { label: 'Move to folder', icon: <FolderIcon />, run: () => setMoving(a) },
                      { label: 'Save as template', icon: <IconSparkle size={16} color={INK} />, run: () => setTemplating(a) },
                      { label: 'Delete', icon: <IconTrashOutline size={16} color={INK} />, run: () => setDeleting(a) },
                    ] : [
                      { label: 'Open', icon: <IconPencil size={16} color={INK} />, run: () => onOpen(a.id) },
                      ...(a.mode !== 'off'
                        ? [{ label: 'Switch off', icon: <PowerIcon />, run: () => switchOff.mutate(a.id) }]
                        : []),
                    ]} />
                </Td>
              </tr>
            ))}
            {agents.isSuccess && rows.length === 0 && (
              <tr><td colSpan={8}>
                <Empty>
                  {needle ? `No agents match “${q.trim()}”.`
                    : admin ? 'No agents yet. Create one — it starts Off, and nothing runs until you publish it and switch it on.'
                      : 'No agents yet.'}
                </Empty>
              </td></tr>
            )}
            {agents.isError && (
              <tr><td colSpan={8} role="alert" style={{ padding: 24, fontSize: 13, color: 'rgb(180,35,24)' }}>
                {(agents.error as Error).message}
              </td></tr>
            )}
          </tbody>
        </table>
      </TableCard>

      {creating && (
        <CreateAgentModal initialTemplate={creating.template} folderId={folder}
          onClose={() => setCreating(null)}
          onCreated={(id) => { setCreating(null); refresh(); onOpen(id) }} />
      )}
      {moving && (
        <MoveToFolderModal agent={moving} onClose={() => setMoving(null)}
          onMoved={() => { setMoving(null); refresh(); setNotice('Moved.') }} />
      )}
      {templating && (
        <SaveTemplateModal agent={templating} onClose={() => setTemplating(null)}
          onSaved={(name) => { setTemplating(null); refresh(); setNotice(`Saved template “${name}”.`) }} />
      )}
      {deleting && (
        <Confirm title={`Delete “${deleting.name}”?`} danger confirmLabel="Delete" busy={remove.isPending}
          body="The agent is switched off and removed from this list, and anything it had queued is cancelled. Its run logs, versions and suggestions are kept."
          onCancel={() => setDeleting(null)} onConfirm={() => remove.mutate(deleting.id)} />
      )}
      {folderDialog && (
        <FolderModal initial={folderDialog} onClose={() => setFolderDialog(null)}
          onSaved={() => { setFolderDialog(null); refresh() }} />
      )}
    </div>
  )
}

function FolderIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={INK} strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  )
}

function PowerIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={INK} strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3v9" /><path d="M6.4 6.4a8 8 0 1 0 11.2 0" />
    </svg>
  )
}

function FolderPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button type="button" role="tab" aria-selected={active} onClick={onClick}
      style={{ height: 26, padding: '0 10px', borderRadius: 13, fontSize: 12, fontWeight: 500,
        color: active ? 'rgb(0,78,235)' : 'rgb(71,84,103)',
        backgroundColor: active ? 'rgb(239,244,255)' : 'transparent',
        border: `1px solid ${active ? 'rgb(178,204,255)' : LINE}` }}>
      {label}
    </button>
  )
}

function CreateAgentModal({ initialTemplate, folderId, onClose, onCreated }: {
  initialTemplate: number | null; folderId: number | null
  onClose: () => void; onCreated: (id: number) => void
}) {
  const folders = useQuery({ queryKey: ['ai-folders'], queryFn: aiFolders })
  const templates = useQuery({ queryKey: ['ai-templates'], queryFn: aiTemplates })
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [folder, setFolder] = useState<string>(folderId != null ? String(folderId) : '')
  const [template, setTemplate] = useState<string>(initialTemplate != null ? String(initialTemplate) : '')
  const [channel, setChannel] = useState<'text' | 'voice'>('text')
  const [error, setError] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => createAiAgent({ name: name.trim(), description: description.trim() || null,
      folder_id: folder ? Number(folder) : null, template_id: template ? Number(template) : null, channel }),
    onSuccess: (a) => onCreated(a.id), onError: (e: Error) => setError(e.message),
  })
  return (
    <Modal title="Create agent" subtitle="It starts Off." onClose={onClose}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}
          style={{ ...primarySmall, opacity: !name.trim() || create.isPending ? 0.5 : 1 }}>Create</button>
      </>}>
      <Field label="Agent name" required>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} style={INPUT}
          placeholder="e.g. Missed call follow-up" aria-label="Agent name" />
      </Field>
      <Field label="Description">
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} style={textarea}
          aria-label="Description" />
      </Field>
      {!template && (
        <Field label="Channel" hint={channel === 'voice'
          ? 'Answers phone calls. Edited here, published to the phone system (owen-main), which runs the call.'
          : 'Texts customers when a trigger fires.'}>
          <select value={channel} onChange={(e) => setChannel(e.target.value === 'voice' ? 'voice' : 'text')}
            style={INPUT} aria-label="Channel">
            <option value="text">{channelLabel('text')}</option>
            <option value="voice">{channelLabel('voice')}</option>
          </select>
        </Field>
      )}
      <Field label="Folder">
        <select value={folder} onChange={(e) => setFolder(e.target.value)} style={INPUT} aria-label="Folder">
          <option value="">No folder</option>
          {(folders.data ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </Field>
      {(templates.data ?? []).length > 0 && (
        <Field label="Start from template">
          <select value={template} onChange={(e) => setTemplate(e.target.value)} style={INPUT}
            aria-label="Start from template">
            <option value="">Blank agent</option>
            {(templates.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </Field>
      )}
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}

function MoveToFolderModal({ agent, onClose, onMoved }: { agent: AiAgentRow; onClose: () => void; onMoved: () => void }) {
  const folders = useQuery({ queryKey: ['ai-folders'], queryFn: aiFolders })
  const [folder, setFolder] = useState(agent.folder_id != null ? String(agent.folder_id) : '')
  const [error, setError] = useState<string | null>(null)
  const move = useMutation({
    mutationFn: () => updateAiAgent(agent.id, { folder_id: folder ? Number(folder) : null }),
    onSuccess: onMoved, onError: (e: Error) => setError(e.message),
  })
  return (
    <Modal title={`Move “${agent.name}”`} onClose={onClose} width={420}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" onClick={() => move.mutate()} style={primarySmall}>Move</button>
      </>}>
      <Field label="Folder">
        <select value={folder} onChange={(e) => setFolder(e.target.value)} style={INPUT} aria-label="Folder">
          <option value="">No folder</option>
          {(folders.data ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </Field>
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}

function SaveTemplateModal({ agent, onClose, onSaved }: { agent: AiAgentRow; onClose: () => void; onSaved: (name: string) => void }) {
  const [name, setName] = useState(agent.name)
  const [description, setDescription] = useState(agent.description ?? '')
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => saveAiTemplate({ agent_id: agent.id, name: name.trim(), description: description.trim() || null }),
    onSuccess: () => onSaved(name.trim()), onError: (e: Error) => setError(e.message),
  })
  return (
    <Modal title="Save as template" subtitle="A copy of this agent's draft, to start new agents from. No API key is ever part of a template."
      onClose={onClose}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" disabled={!name.trim()} onClick={() => save.mutate()}
          style={{ ...primarySmall, opacity: name.trim() ? 1 : 0.5 }}>Save template</button>
      </>}>
      <Field label="Template name" required>
        <input value={name} onChange={(e) => setName(e.target.value)} style={INPUT} aria-label="Template name" />
      </Field>
      <Field label="Description">
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} style={textarea}
          aria-label="Template description" />
      </Field>
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}

function FolderModal({ initial, onClose, onSaved }: {
  initial: { id: number | null; name: string }; onClose: () => void; onSaved: () => void
}) {
  const [name, setName] = useState(initial.name)
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => (initial.id == null ? createAiFolder(name.trim()) : renameAiFolder(initial.id, name.trim())),
    onSuccess: onSaved, onError: (e: Error) => setError(e.message),
  })
  return (
    <Modal title={initial.id == null ? 'New folder' : 'Rename folder'} onClose={onClose} width={420}
      footer={<>
        <button type="button" onClick={onClose} style={smallButton}>Cancel</button>
        <button type="button" disabled={!name.trim()} onClick={() => save.mutate()}
          style={{ ...primarySmall, opacity: name.trim() ? 1 : 0.5 }}>Save</button>
      </>}>
      <Field label="Folder name" required>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} style={INPUT} aria-label="Folder name" />
      </Field>
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}
