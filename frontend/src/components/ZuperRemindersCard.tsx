import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  reminderLanguage, reminderLog, reminderStatus, reminderUpcoming, saveReminderSettings,
  setReminderLanguage,
  type ReminderKind, type ReminderLang, type ReminderStatus,
} from '../lib/api'
import { segmentInfo } from '../lib/smsSegments'
import {
  BORDER, BUTTON, DANGER, DIVIDER, ErrorLine, FAINT, INPUT, MUTED, PRIMARY_BUTTON, TEXT,
} from './opportunity/ui'

/**
 * Settings → Automations: "Appointment reminders (from Zuper)" (2026-10-08,
 * backend/app/reminders). The ONE rule on the page with a switch: Owen lifted the "no
 * automatic texts" decision for these two reminders only, and it must be easy to switch off
 * once Zuper's own workflows can text.
 *
 * Read by ADMIN and unrestricted DISPATCHER (the server's gate); only an ADMIN changes it.
 * Test / On need the typed phrase, Off is one press. Times in America/New_York.
 * OUR design, styled like the rule cards above it.
 */
const KIND_LABEL: Record<ReminderKind, string> = {
  day_before: 'Day before (from 10 AM)', four_hour: '4 hours before',
  ahs_submitted: 'Report submitted to AHS',
}
const LANG_LABEL: Record<ReminderLang, string> = { en: 'English', es: 'Spanish' }
const STATE_LABEL: Record<string, string> = {
  sent: 'Sent', refused: 'Refused', failed: 'Failed', suppressed: 'Not sent (DND)',
  waiting: 'Held until 8 AM', cancelled: 'Not sent',
  skipped: 'Skipped', would_send: 'Would send (test)', sending: 'Sending',
}

function ny(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  })
}

const MODE_TEXT = { off: 'Off', test: 'Test', on: 'On' } as const

export function ZuperRemindersCard({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['reminders'], queryFn: reminderStatus })
  const log = useQuery({ queryKey: ['reminders', 'log'], queryFn: reminderLog })
  const upcoming = useQuery({ queryKey: ['reminders', 'upcoming'], queryFn: reminderUpcoming })
  // Which switch is being turned on, and to what — the reminders or the AHS text.
  const [asking, setAsking] = useState<{ field: 'mode' | 'ahs_submitted_mode';
    to: 'test' | 'on' } | null>(null)
  const [phrase, setPhrase] = useState('')
  const save = useMutation({
    mutationFn: saveReminderSettings,
    onSuccess: (s) => {
      qc.setQueryData(['reminders'], s)
      qc.invalidateQueries({ queryKey: ['reminders'] })
      setAsking(null)
      setPhrase('')
    },
  })
  const s = status.data
  if (status.error) return <ErrorLine error={(status.error as Error).message} />
  if (!s) return null
  const on = s.sending
  return (
    <div role="listitem" data-rule="zuper_reminders" data-enabled={on}
      style={{ border: '1px solid ' + DIVIDER, borderRadius: 8, padding: '12px 16px',
        marginTop: 12, backgroundColor: '#fff' }}>
      <div className="flex items-center gap-3">
        <div style={{ fontSize: 14, fontWeight: 600, color: TEXT }}>
          Appointment reminders (from Zuper)
        </div>
        <span data-testid="rule-state" style={{
          marginLeft: 'auto', padding: '2px 10px', borderRadius: 12, fontSize: 12, fontWeight: 500,
          color: on ? 'rgb(2,122,72)' : 'rgb(71,84,103)',
          backgroundColor: on ? 'rgb(236,253,243)' : 'rgb(242,244,247)',
        }}>{on ? MODE_TEXT[s.mode] : 'Off'}</span>
      </div>
      <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>
        When: the day before a visit (from {s.times.day_before_from}) and {s.times.four_hour} hours
        before it, never between {s.times.quiet_from} and {s.times.quiet_until} · texts the customer
      </div>
      <div style={{ fontSize: 13, color: TEXT, marginTop: 6 }}>{s.sentence}</div>
      <div style={{ fontSize: 13, color: FAINT, marginTop: 6 }}>
        Only jobs in these Zuper columns: {Object.entries(s.columns).map(([b, cols]) =>
          `${b} → ${cols.join(', ')}`).join(' · ')}. Owen lifted the “no automatic texts” rule
        for these two reminders only (2026-10-08); switch them off when Zuper's own workflows
        take over.
      </div>
      {s.heartbeat.last_error && (
        <div role="alert" style={{ fontSize: 13, color: DANGER, marginTop: 6 }}>
          {s.heartbeat.last_error} ({ny(s.heartbeat.last_error_at)})
        </div>
      )}

      {isAdmin && (
        <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 12 }}>
          {(['off', 'test', 'on'] as const).map((m) => (
            <button key={m} type="button" aria-pressed={s.mode === m}
              disabled={save.isPending || s.mode === m}
              onClick={() => (m === 'off' ? save.mutate({ mode: 'off' })
                : setAsking({ field: 'mode', to: m }))}
              style={s.mode === m ? PRIMARY_BUTTON : BUTTON}>{MODE_TEXT[m]}</button>
          ))}
        </div>
      )}

      <div data-rule="ahs_submitted" data-enabled={s.ahs_submitted.sending}
        style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid ' + DIVIDER }}>
        <div className="flex items-center gap-3">
          <div style={{ fontSize: 14, fontWeight: 600, color: TEXT }}>
            Inspection report submitted to AHS
          </div>
          <span data-testid="ahs-state" style={{
            marginLeft: 'auto', padding: '2px 10px', borderRadius: 12, fontSize: 12, fontWeight: 500,
            color: s.ahs_submitted.sending ? 'rgb(2,122,72)' : 'rgb(71,84,103)',
            backgroundColor: s.ahs_submitted.sending ? 'rgb(236,253,243)' : 'rgb(242,244,247)',
          }}>{s.ahs_submitted.sending ? MODE_TEXT[s.ahs_submitted.mode] : 'Off'}</span>
        </div>
        <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>
          When: a job on {s.ahs_submitted.board} is moved to {s.ahs_submitted.columns.join(' or ')}
          {' '}— once per job; moves at night go at 8 AM · texts the customer
        </div>
        <div style={{ fontSize: 13, color: TEXT, marginTop: 6 }}>{s.ahs_submitted.sentence}</div>
        {isAdmin && (
          <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 8 }}>
            {(['off', 'test', 'on'] as const).map((m) => (
              <button key={m} type="button" aria-pressed={s.ahs_submitted.mode === m}
                disabled={save.isPending || s.ahs_submitted.mode === m}
                onClick={() => (m === 'off' ? save.mutate({ ahs_submitted_mode: 'off' })
                  : setAsking({ field: 'ahs_submitted_mode', to: m }))}
                style={s.ahs_submitted.mode === m ? PRIMARY_BUTTON : BUTTON}>{MODE_TEXT[m]}</button>
            ))}
          </div>
        )}
      </div>
      {asking && (
        <div role="dialog" aria-label="Turn reminders on" style={{ marginTop: 10, padding: 12,
          border: '1px solid ' + BORDER, borderRadius: 8 }}>
          <div style={{ fontSize: 13, color: TEXT }}>
            {asking.to === 'test'
              ? 'Test: only the test numbers below are texted; every other text is recorded as “would send”.'
              : asking.field === 'mode'
                ? 'On: real customers are texted the day before and 4 hours before their visit.'
                : 'On: real customers are texted when their job moves to Submit To AHS / Awaiting AHS Decision.'}
            {' '}Type <b>TURN ON</b> to confirm.
          </div>
          <div className="flex items-center gap-2" style={{ marginTop: 8 }}>
            <input aria-label="Type TURN ON" value={phrase} onChange={(e) => setPhrase(e.target.value)}
              style={{ ...INPUT, width: 160 }} />
            <button type="button" style={PRIMARY_BUTTON} disabled={phrase !== 'TURN ON' || save.isPending}
              onClick={() => save.mutate({ [asking.field]: asking.to, confirm: phrase })}>
              Confirm</button>
            <button type="button" style={BUTTON} onClick={() => { setAsking(null); setPhrase('') }}>
              Cancel</button>
          </div>
        </div>
      )}
      <ErrorLine error={save.error ? (save.error as Error).message : null} />

      {isAdmin && <TestNumbers s={s} onSave={(nums) => save.mutate({ test_numbers: nums })} />}
      <Wording s={s} isAdmin={isAdmin} onSave={(kind, lang, text) =>
        save.mutate({ templates: { [kind]: { [lang]: text } } })} />

      <details style={{ marginTop: 12 }}>
        <summary style={{ fontSize: 13, fontWeight: 600, color: TEXT, cursor: 'pointer' }}>
          Visits today and tomorrow ({upcoming.data?.visits.length ?? 0})
        </summary>
        <table style={{ width: '100%', fontSize: 12, marginTop: 6, borderCollapse: 'collapse' }}>
          <thead><tr style={{ color: FAINT, textAlign: 'left' }}>
            <th>Job</th><th>Column</th><th>Visit</th><th>Language</th><th>Reminders</th>
          </tr></thead>
          <tbody>
            {upcoming.data?.visits.map((v) => (
              <tr key={v.job_uid} style={{ borderTop: '1px solid ' + DIVIDER }}>
                <td>#{v.job_number}</td>
                <td>{v.board} / {v.status}</td>
                <td>{ny(v.visit_start)}</td>
                <td>{v.language ? LANG_LABEL[v.language] : (v.has_mobile ? '—' : 'no mobile')}</td>
                <td>{v.counts ? v.reminders.map((r) => `${r.kind === 'day_before' ? 'Day before'
                  : '4 h'}: ${r.state}`).join(' · ') : 'column does not get reminders'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <details style={{ marginTop: 8 }}>
        <summary style={{ fontSize: 13, fontWeight: 600, color: TEXT, cursor: 'pointer' }}>
          Reminder log ({log.data?.reminders.length ?? 0})
        </summary>
        <table style={{ width: '100%', fontSize: 12, marginTop: 6, borderCollapse: 'collapse' }}>
          <thead><tr style={{ color: FAINT, textAlign: 'left' }}>
            <th>When</th><th>Job</th><th>Reminder</th><th>Visit</th><th>Language</th><th>Result</th>
          </tr></thead>
          <tbody>
            {log.data?.reminders.map((r) => (
              <tr key={r.id} title={r.body ?? ''} style={{ borderTop: '1px solid ' + DIVIDER }}>
                <td>{ny(r.created_at)}</td>
                <td>#{r.job_number}</td>
                <td>{KIND_LABEL[r.kind] ?? r.kind}</td>
                <td>{ny(r.visit_start)}</td>
                <td>{r.language ? LANG_LABEL[r.language] : '—'}</td>
                <td>{STATE_LABEL[r.state] ?? r.state}{r.reason ? ` — ${r.reason}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}

function TestNumbers({ s, onSave }: { s: ReminderStatus; onSave: (n: string[]) => void }) {
  const [text, setText] = useState(s.test_numbers.join(', '))
  const changed = text !== s.test_numbers.join(', ')
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: TEXT }}>Test numbers</div>
      <div className="flex items-center gap-2" style={{ marginTop: 4 }}>
        <input aria-label="Test numbers" value={text} onChange={(e) => setText(e.target.value)}
          placeholder="(941) 555-0100, …" style={{ ...INPUT, flex: 1 }} />
        <button type="button" style={BUTTON} disabled={!changed}
          onClick={() => onSave(text.split(',').map((x) => x.trim()).filter(Boolean))}>Save</button>
      </div>
    </div>
  )
}

function Wording({ s, isAdmin, onSave }: {
  s: ReminderStatus; isAdmin: boolean
  onSave: (kind: ReminderKind, lang: ReminderLang, text: string) => void
}) {
  const [kind, setKind] = useState<ReminderKind>('day_before')
  const [lang, setLang] = useState<ReminderLang>('en')
  const current = s.templates[kind][lang]
  const [draft, setDraft] = useState<string | null>(null)
  const text = draft ?? current
  const seg = segmentInfo(text)
  return (
    <div style={{ marginTop: 12 }}>
      <div className="flex items-center gap-2">
        <div style={{ fontSize: 13, fontWeight: 600, color: TEXT }}>Wording</div>
        <select aria-label="Reminder" value={kind} style={{ ...INPUT, width: 'auto', height: 30 }}
          onChange={(e) => { setKind(e.target.value as ReminderKind); setDraft(null) }}>
          <option value="day_before">Day before</option>
          <option value="four_hour">4 hours before</option>
          <option value="ahs_submitted">Report submitted to AHS</option>
        </select>
        <select aria-label="Language" value={lang} style={{ ...INPUT, width: 'auto', height: 30 }}
          onChange={(e) => { setLang(e.target.value as ReminderLang); setDraft(null) }}>
          <option value="en">English</option>
          <option value="es">Spanish</option>
        </select>
      </div>
      <textarea aria-label="Reminder wording" value={text} readOnly={!isAdmin} rows={3}
        onChange={(e) => setDraft(e.target.value)}
        style={{ ...INPUT, width: '100%', height: 'auto', marginTop: 6, padding: 8 }} />
      <div className="flex items-center gap-2" style={{ fontSize: 12, color: FAINT }}>
        <span>{'{first_name}'}, {'{day}'} and {'{window}'} (“from 2 PM to 4 PM”) are filled in · {seg.characters} characters
          · {seg.segments} segment{seg.segments === 1 ? '' : 's'}</span>
        {isAdmin && (
          <>
            <button type="button" style={{ ...BUTTON, marginLeft: 'auto' }}
              disabled={text === s.default_templates[kind][lang]}
              onClick={() => { onSave(kind, lang, ''); setDraft(null) }}>Use default</button>
            <button type="button" style={PRIMARY_BUTTON} disabled={draft === null || draft === current}
              onClick={() => { onSave(kind, lang, text); setDraft(null) }}>Save wording</button>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * The contact panel's "Reminder language" row (2026-10-08): the language this customer's
 * appointment reminders go out in — worked out from their own texts and calls, or set by a
 * person (which then always wins). Drawn for the Dispatch audience only; the server refuses
 * everyone else. `phone` is the contact's number.
 */
export function ReminderLanguageRow({ phone, labelStyle, valueStyle }: {
  phone: string
  labelStyle: React.CSSProperties
  valueStyle: React.CSSProperties
}) {
  const qc = useQueryClient()
  const lang = useQuery({ queryKey: ['reminder-language', phone],
    queryFn: () => reminderLanguage(phone), retry: false })
  const set = useMutation({
    mutationFn: (v: ReminderLang | null) => setReminderLanguage(phone, v),
    onSuccess: (r) => qc.setQueryData(['reminder-language', phone], r),
  })
  if (!lang.data) return null
  const d = lang.data
  const ev = d.evidence ?? {}
  const why = d.source === 'manual' ? 'set by hand'
    : (ev.es_texts || ev.es_calls || ev.en_texts || ev.en_calls)
      ? `from ${(ev.es_texts ?? 0) + (ev.en_texts ?? 0)} texts and ${(ev.es_calls ?? 0) +
        (ev.en_calls ?? 0)} calls`
      : 'no messages yet — English'
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={labelStyle}>Reminder language</div>
      <div className="flex items-center gap-2" style={{ ...valueStyle, marginTop: 4 }}>
        <select aria-label="Reminder language" disabled={set.isPending}
          value={d.source === 'manual' ? d.language : 'auto'}
          onChange={(e) => set.mutate(e.target.value === 'auto' ? null
            : e.target.value as ReminderLang)}
          style={{ fontSize: 13, border: '1px solid ' + BORDER, borderRadius: 6, padding: '2px 6px' }}>
          <option value="auto">Automatic ({LANG_LABEL[d.language]})</option>
          <option value="en">English</option>
          <option value="es">Spanish</option>
        </select>
        <span style={{ fontSize: 12, color: FAINT }}>{why}</span>
      </div>
    </div>
  )
}
