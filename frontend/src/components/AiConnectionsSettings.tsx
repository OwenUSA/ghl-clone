import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  aiCatalogue, aiConnections, aiSettings, createAiConnection, deleteAiConnection, loadAiModels,
  saveAiSettings, testAiConnection, updateAiConnection, type AiConnection,
} from '../lib/api'
import {
  connectionBody, connectionProblems, DEFAULT_MODEL, formatCost, knownPrice, type ConnectionForm,
} from '../lib/aiAgents'
import { IconPencil, IconPlus, IconTrashOutline } from './PipelineIcons'
import {
  Confirm, Empty, Field, INPUT, LINE, Modal, MUTED, Notice, PageHeader, primarySmall, smallButton,
  Switch, TableCard, Td, TEXT, Th, FAINT,
} from './ai/aiUi'

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: 'Anthropic', openai: 'OpenAI', openai_compatible: 'OpenAI-compatible',
}

/**
 * Settings → AI Connections (ADMIN, 2026-09-15): the global "Pause all AI agents" switch,
 * the on-call phone an emergency escalation texts, and the provider accounts. A key is
 * write-only: the table shows "•••• last4" and an edit with the key left blank keeps it.
 */
export function AiConnectionsSettings() {
  const qc = useQueryClient()
  const settings = useQuery({ queryKey: ['ai-settings'], queryFn: aiSettings })
  const conns = useQuery({ queryKey: ['ai-connections'], queryFn: aiConnections })
  const [dialog, setDialog] = useState<AiConnection | 'new' | null>(null)
  const [deleting, setDeleting] = useState<AiConnection | null>(null)
  const [phone, setPhone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmPause, setConfirmPause] = useState<boolean | null>(null)

  const saveSettings = useMutation({
    mutationFn: saveAiSettings,
    onSuccess: (s) => {
      qc.setQueryData(['ai-settings'], s); setPhone(null); setConfirmPause(null); setError(null)
      setNotice(s.paused ? 'All AI agents are paused. Nothing runs until you switch this off.' : 'Saved.')
    },
    onError: (e: Error) => { setConfirmPause(null); setNotice(null); setError(e.message) },
  })
  const remove = useMutation({
    mutationFn: deleteAiConnection,
    onSuccess: () => { setDeleting(null); qc.invalidateQueries({ queryKey: ['ai-connections'] }) },
    onError: (e: Error) => { setDeleting(null); setError(e.message) },
  })

  const s = settings.data
  const phoneValue = phone ?? s?.on_call_phone_display ?? s?.on_call_phone ?? ''

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '18px 32px 32px' }}>
      <PageHeader title="AI Connections"
        subtitle="The AI provider accounts your agents use, and the switch that stops every agent at once.">
        <button type="button" onClick={() => setDialog('new')} style={primarySmall}>
          <IconPlus size={14} color="#fff" /> Add connection
        </button>
      </PageHeader>
      <Notice error={error} notice={notice} onDismiss={() => { setError(null); setNotice(null) }} />

      {s && (
        <div className="bg-white" style={{ marginTop: 12, border: `1px solid ${LINE}`, borderRadius: 8 }}>
          <div className="flex items-center gap-16" style={{ padding: 16, borderBottom: `1px solid ${LINE}` }}>
            <div className="flex-1">
              <div style={{ fontSize: 14, fontWeight: 500, color: TEXT }}>Pause all AI agents</div>
              <div style={{ fontSize: 13, color: MUTED, marginTop: 2 }}>
                Checked before every run. While paused no agent calls a model or acts, and each trigger is logged as skipped.
              </div>
            </div>
            <Switch checked={s.paused} label="Pause all AI agents"
              onChange={(v) => setConfirmPause(v)} />
          </div>
          <div style={{ padding: '2px 16px 16px' }}>
            <div className="flex items-end gap-12">
              <div style={{ width: 280 }}>
                <Field label="On-call phone">
                  <input value={phoneValue} onChange={(e) => setPhone(e.target.value)} style={INPUT}
                    aria-label="On-call phone" placeholder="(941) 555-0100" />
                </Field>
              </div>
              <button type="button" disabled={phone == null}
                onClick={() => saveSettings.mutate({ on_call_phone: (phone ?? '').trim() || null })}
                style={{ ...smallButton, height: 36, opacity: phone == null ? 0.5 : 1 }}>
                Save phone
              </button>
            </div>
            <div style={{ fontSize: 12, color: FAINT, marginTop: 6 }}>
              An EMERGENCY escalation texts this number from (954) 482-9099, through the phone system — only while an agent is on Auto-pilot, or when staff approve its suggestion.
            </div>
          </div>
        </div>
      )}

      {s && !s.secrets_configured && (
        <div role="alert" style={{ marginTop: 12, fontSize: 13, borderRadius: 8, padding: '10px 12px',
          color: 'rgb(181,71,8)', backgroundColor: 'rgb(255,250,235)', border: '1px solid rgb(254,223,137)' }}>
          Connections cannot be saved yet: the server has no AI_SECRETS_KEY, which encrypts API keys at rest.
          The operator must set it and restart the API and the worker.
        </div>
      )}

      <TableCard>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 900 }}>
          <thead><tr>
            <Th>Name</Th><Th width={160}>Provider</Th><Th width={180}>Default model</Th><Th width={120}>API key</Th>
            <Th width={170}>Price per 1M in / out</Th><Th width={90} align="right">Agents</Th><Th width={100} align="center">Actions</Th>
          </tr></thead>
          <tbody>
            {(conns.data ?? []).map((c) => (
              <tr key={c.id}>
                <Td>
                  <div style={{ fontWeight: 500 }}>{c.name}</div>
                  {c.base_url && <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{c.base_url}</div>}
                </Td>
                <Td>{PROVIDER_LABEL[c.provider] ?? c.provider}</Td>
                <Td>{c.default_model}</Td>
                <Td><span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>{c.api_key}</span></Td>
                <Td>{c.price_input == null && c.price_output == null ? <span style={{ color: MUTED }}>Unknown — no cost shown</span>
                  : `${formatCost(c.price_input)} / ${formatCost(c.price_output)}`}</Td>
                <Td align="right">{c.agents}</Td>
                <Td align="center">
                  <button type="button" aria-label={`Edit ${c.name}`} onClick={() => setDialog(c)} style={{ padding: 4 }}>
                    <IconPencil size={16} color={MUTED} />
                  </button>
                  <button type="button" aria-label={`Delete ${c.name}`} onClick={() => setDeleting(c)} style={{ padding: 4 }}>
                    <IconTrashOutline size={16} color={MUTED} />
                  </button>
                </Td>
              </tr>
            ))}
            {conns.isSuccess && conns.data.length === 0 && (
              <tr><td colSpan={7}><Empty>No AI connections yet.</Empty></td></tr>
            )}
          </tbody>
        </table>
      </TableCard>

      {dialog && (
        <ConnectionModal connection={dialog === 'new' ? null : dialog} onClose={() => setDialog(null)}
          onSaved={(c) => { setDialog(null); qc.invalidateQueries({ queryKey: ['ai-connections'] }); setNotice(`Saved “${c.name}”.`) }} />
      )}
      {deleting && (
        <Confirm title={`Delete “${deleting.name}”?`} danger confirmLabel="Delete" busy={remove.isPending}
          body="Its encrypted key is deleted. An agent that still uses it must be switched to another connection first."
          onCancel={() => setDeleting(null)} onConfirm={() => remove.mutate(deleting.id)} />
      )}
      {confirmPause != null && (
        <Confirm title={confirmPause ? 'Pause all AI agents?' : 'Resume AI agents?'}
          confirmLabel={confirmPause ? 'Pause all' : 'Resume'} danger={confirmPause} busy={saveSettings.isPending}
          body={confirmPause
            ? 'No agent will run, in any mode, until this is switched off again.'
            : 'Agents in Suggest or Auto-pilot mode start acting on their triggers again.'}
          onCancel={() => setConfirmPause(null)} onConfirm={() => saveSettings.mutate({ paused: confirmPause })} />
      )}
    </div>
  )
}

function ConnectionModal({ connection, onClose, onSaved }: {
  connection: AiConnection | null; onClose: () => void; onSaved: (c: AiConnection) => void
}) {
  const editing = connection != null
  const cat = useQuery({ queryKey: ['ai-catalogue'], queryFn: aiCatalogue })
  const [form, setForm] = useState<ConnectionForm>(() => connection ? {
    name: connection.name, provider: connection.provider, base_url: connection.base_url ?? '', api_key: '',
    default_model: connection.default_model,
    price_input: connection.price_input != null ? String(Number(connection.price_input)) : '',
    price_output: connection.price_output != null ? String(Number(connection.price_output)) : '',
  } : { name: '', provider: 'anthropic', base_url: '', api_key: '', default_model: 'claude-sonnet-5',
    price_input: '2', price_output: '10' })
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [test, setTest] = useState<{ ok: boolean; sentence: string; latency_ms?: number } | null>(null)
  const [models, setModels] = useState<string[] | null>(null)

  const set = (patch: Partial<ConnectionForm>) => {
    setForm((f) => {
      const next = { ...f, ...patch }
      // A known model prefills its list price; a price already typed for an unknown one stays.
      if (patch.default_model !== undefined || patch.provider !== undefined) {
        const hit = cat.data ? knownPrice(cat.data.known_prices, next.default_model) : null
        if (hit) { next.price_input = hit.input; next.price_output = hit.output }
      }
      return next
    })
    setTest(null)
  }
  const problems = connectionProblems(form, editing)
  const probe = () => ({ provider: form.provider, base_url: form.provider === 'openai_compatible' ? form.base_url.trim() : null,
    api_key: form.api_key.trim() || null, model: form.default_model.trim(), connection_id: connection?.id ?? null })

  const save = useMutation({
    mutationFn: () => (editing ? updateAiConnection(connection.id, connectionBody(form, true))
      : createAiConnection(connectionBody(form, false))),
    onSuccess: onSaved, onError: (e: Error) => setError(e.message),
  })
  const runTest = useMutation({
    mutationFn: () => testAiConnection(probe()),
    onSuccess: (r) => { setError(null); setTest(r) }, onError: (e: Error) => setTest({ ok: false, sentence: e.message }),
  })
  const load = useMutation({
    mutationFn: () => loadAiModels(probe()),
    onSuccess: (r) => { if (r.ok) setModels(r.models); else setTest({ ok: false, sentence: r.sentence }) },
    onError: (e: Error) => setTest({ ok: false, sentence: e.message }),
  })
  const canProbe = !!form.default_model.trim() && (!!form.api_key.trim() || editing)
    && (form.provider !== 'openai_compatible' || !!form.base_url.trim())

  return (
    <Modal title={editing ? 'Edit connection' : 'Add connection'} width={620} onClose={onClose}
      subtitle="The API key is encrypted on the server and never shown again."
      footer={<>
        {test && (
          <span role="status" data-test-ok={test.ok ? 'true' : 'false'} className="mr-auto"
            style={{ fontSize: 13, color: test.ok ? 'rgb(2,122,72)' : 'rgb(180,35,24)' }}>
            {test.sentence}
          </span>
        )}
        <button type="button" onClick={() => runTest.mutate()} disabled={!canProbe || runTest.isPending}
          style={{ ...smallButton, height: 36, opacity: !canProbe || runTest.isPending ? 0.5 : 1 }}>
          {runTest.isPending ? 'Testing…' : 'Test'}
        </button>
        <button type="button" onClick={onClose} style={{ ...smallButton, height: 36 }}>Cancel</button>
        <button type="button" onClick={() => { setTouched(true); if (problems.length === 0) save.mutate() }}
          disabled={save.isPending} style={{ ...primarySmall, height: 36, opacity: save.isPending ? 0.6 : 1 }}>
          {editing ? 'Save' : 'Add connection'}
        </button>
      </>}>
      <Field label="Name" required>
        <input autoFocus value={form.name} onChange={(e) => set({ name: e.target.value })} style={INPUT}
          aria-label="Connection name" placeholder="e.g. Claude — office" />
      </Field>
      <Field label="Provider" required>
        {editing ? (
          <div style={{ fontSize: 14, color: TEXT, marginTop: 8 }}>{PROVIDER_LABEL[form.provider]}</div>
        ) : (
          <select value={form.provider} aria-label="Provider" style={INPUT}
            onChange={(e) => set({ provider: e.target.value, default_model: DEFAULT_MODEL[e.target.value] ?? '' })}>
            <option value="anthropic">Anthropic</option>
            <option value="openai">OpenAI</option>
            <option value="openai_compatible">OpenAI-compatible (custom base URL)</option>
          </select>
        )}
      </Field>
      {form.provider === 'openai_compatible' && (
        <Field label="Base URL" required hint="The server's OpenAI-compatible endpoint, e.g. https://example.com/v1">
          <input value={form.base_url} onChange={(e) => set({ base_url: e.target.value })} style={INPUT} aria-label="Base URL" />
        </Field>
      )}
      <Field label="API key" required={!editing} hint={editing ? `Stored key ${connection.api_key}. Leave blank to keep it.` : undefined}>
        <input type="password" autoComplete="off" value={form.api_key} onChange={(e) => set({ api_key: e.target.value })}
          style={INPUT} aria-label="API key" placeholder={editing ? connection.api_key : ''} />
      </Field>
      <Field label="Default model" required>
        <div className="flex items-center gap-8">
          <input value={form.default_model} onChange={(e) => set({ default_model: e.target.value })}
            style={INPUT} aria-label="Default model" list="ai-model-list" />
          <button type="button" onClick={() => load.mutate()} disabled={!canProbe || load.isPending}
            style={{ ...smallButton, height: 36, marginTop: 8, opacity: !canProbe || load.isPending ? 0.5 : 1 }}>
            {load.isPending ? 'Loading…' : 'Load models'}
          </button>
        </div>
        {models && (
          <datalist id="ai-model-list">{models.map((m) => <option key={m} value={m} />)}</datalist>
        )}
      </Field>
      {models && <div style={{ fontSize: 12, color: FAINT, marginTop: 4 }}>{models.length} models available — pick one from the list.</div>}
      <div className="flex gap-12">
        <div className="flex-1">
          <Field label="Price per 1M input tokens ($)" hint="Optional. Blank = cost unknown.">
            <input value={form.price_input} onChange={(e) => set({ price_input: e.target.value })} style={INPUT}
              aria-label="Price per 1M input tokens" inputMode="decimal" />
          </Field>
        </div>
        <div className="flex-1">
          <Field label="Price per 1M output tokens ($)">
            <input value={form.price_output} onChange={(e) => set({ price_output: e.target.value })} style={INPUT}
              aria-label="Price per 1M output tokens" inputMode="decimal" />
          </Field>
        </div>
      </div>
      {touched && problems.length > 0 && (
        <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{problems.join(' ')}</div>
      )}
      {error && <div role="alert" style={{ marginTop: 12, fontSize: 13, color: 'rgb(180,35,24)' }}>{error}</div>}
    </Modal>
  )
}
