import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { aiCatalogue, aiTemplates, deleteAiTemplate, type AiTemplate } from '../../lib/api'
import type { Me } from '../../lib/auth'
import { isAiAdmin, stamp, TRIGGER_LABEL } from '../../lib/aiAgents'
import { Confirm, Empty, MUTED, Notice, PageHeader, TableCard, Td, Th, BLUE } from './aiUi'

/** Templates: saved agent drafts to start new agents from. None ship with the product. */
export function TemplatesTab({ user, onUse }: { user: Me; onUse: (id: number) => void }) {
  const qc = useQueryClient()
  const admin = isAiAdmin(user)
  const templates = useQuery({ queryKey: ['ai-templates'], queryFn: aiTemplates })
  const cat = useQuery({ queryKey: ['ai-catalogue'], queryFn: aiCatalogue })
  const [deleting, setDeleting] = useState<AiTemplate | null>(null)
  const [error, setError] = useState<string | null>(null)
  const remove = useMutation({
    mutationFn: deleteAiTemplate,
    onSuccess: () => { setDeleting(null); qc.invalidateQueries({ queryKey: ['ai-templates'] }) },
    onError: (e: Error) => { setDeleting(null); setError(e.message) },
  })
  const actionLabel = (name: string) => cat.data?.actions.find((a) => a.name === name)?.label ?? name
  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 16px' }}>
      <PageHeader title="Templates" subtitle="Start a new agent from a saved configuration." />
      <Notice error={error} onDismiss={() => setError(null)} />
      <TableCard>
        <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 760 }}>
          <thead><tr>
            <Th>Template</Th><Th width={260}>Actions it may use</Th><Th width={200}>Triggers</Th>
            <Th width={170}>Created</Th>{admin && <Th width={210} align="center">Actions</Th>}
          </tr></thead>
          <tbody>
            {(templates.data ?? []).map((t) => (
              <tr key={t.id}>
                <Td>
                  <div style={{ fontWeight: 500 }}>{t.name}</div>
                  {t.description && <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{t.description}</div>}
                </Td>
                <Td><span style={{ fontSize: 12, color: MUTED }}>{t.actions.map(actionLabel).join(', ') || '—'}</span></Td>
                <Td><span style={{ fontSize: 12, color: MUTED }}>{t.triggers.map((x) => TRIGGER_LABEL[x] ?? x).join(', ') || '—'}</span></Td>
                <Td>{stamp(t.created_at)}</Td>
                {admin && (
                  <Td align="center">
                    <button type="button" onClick={() => onUse(t.id)} style={{ fontSize: 13, color: BLUE, fontWeight: 500, marginRight: 14 }}>
                      Create agent from template
                    </button>
                    <button type="button" onClick={() => setDeleting(t)} style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>
                      Delete
                    </button>
                  </Td>
                )}
              </tr>
            ))}
            {templates.isSuccess && templates.data.length === 0 && (
              <tr><td colSpan={5}><Empty>
                No templates yet. Open an agent's ⋮ menu on the Agents tab and choose “Save as template”.
              </Empty></td></tr>
            )}
          </tbody>
        </table>
      </TableCard>
      {deleting && (
        <Confirm title={`Delete template “${deleting.name}”?`} danger confirmLabel="Delete" busy={remove.isPending}
          body="Agents already made from it are not affected." onCancel={() => setDeleting(null)}
          onConfirm={() => remove.mutate(deleting.id)} />
      )}
    </div>
  )
}
