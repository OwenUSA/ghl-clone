/**
 * What to tell the operator happened to a message they just sent.
 *
 * Import-free on purpose, like `reminders.ts` and `calendarGrid.ts`, so node can
 * execute it directly and `backend/tests/test_send_outcome.py` can assert the real
 * mapping rather than pattern-match the source. The same reasoning applies here as
 * there, and more sharply: the entire point of this text is that a message which did
 * NOT go must never read as one that did, and a regex asserting "the file mentions
 * refused" would pass against wording that says the opposite.
 *
 * The input is the backend's own `reason` from
 * `POST /api/conversations/{id}/messages` (see `_outcome_reason` and `send_outbound`
 * in backend/app/automations.py):
 *
 *   "queued"            handed to owen-main, on its way to the carrier
 *   "sent"              recorded by the stub transport — NOT transmitted
 *   "recorded"          an internal note; it was never going to be transmitted
 *   "refused: <why>"    the phone system declined it, and said why
 *   "failed: <why>"     we could not reach the phone system — a retry may work
 *   "suppressed: <why>" our own rule stopped it and NOTHING was written
 */
export function sendSentence(reason: string): string {
  if (reason === 'queued')
    return 'Sent. Waiting for the carrier to confirm it arrived.'

  // The stub transport's word for "recorded, transmitted nothing". It says "sent"
  // because the CLI and three backend tests already expect that word on the wire;
  // what the operator is told has to be the truth rather than the wire word.
  if (reason === 'sent')
    return 'Recorded on the thread, but NOT sent — this CRM is not connected to '
      + 'the phone system yet.'

  if (reason === 'recorded') return 'Note added. Internal only — the customer never sees it.'

  if (reason.startsWith('refused: '))
    return 'Not sent. ' + capitalise(reason.slice('refused: '.length))

  // A failure is not a refusal: nothing said no, the phone system just could not be
  // asked. That one can be retried, and the operator is told so (2026-09-15).
  if (reason.startsWith('failed: '))
    return 'Not sent. ' + capitalise(reason.slice('failed: '.length))
      + ' You can retry it from the message.'

  // Our own suppression: DND, or no phone number. Nothing was written at all, so
  // the operator still has their text and needs to know it is going nowhere.
  if (reason.startsWith('suppressed: '))
    return 'Not sent: ' + reason.slice('suppressed: '.length) + '.'

  // An outcome this function has not been taught. Show it rather than swallow it —
  // an unrecognised result reported as a cheerful "Sent." is the exact silence this
  // whole surface exists to remove.
  return 'Result: ' + reason
}

function capitalise(text: string): string {
  if (!text) return ''
  const done = text.charAt(0).toUpperCase() + text.slice(1)
  return /[.!?]$/.test(done) ? done : done + '.'
}

/** Only the fields of a thread event the two functions below read. */
export type DeliveryFacts = { direction: string; type: string; delivery_status: string | null;
  body?: string | null }

/**
 * The sentence under a bubble, or null when the status explains itself (2026-09-15).
 *
 *  - REFUSED carries owen-main's answer, already turned into a sentence on the server
 *    (opted out, blocked, switched off). Shown as it is.
 *  - FAILED says what went wrong AND that it can be retried: the phone system could not
 *    be reached, which a second try may fix.
 *  - LOGGED_ONLY needs none: its label already reads "not sent (recorded only)", and a
 *    sentence under every bubble of an unarmed test database is noise.
 *  - QUEUED / SENT / DELIVERED need no sentence — the label is the whole story.
 */
export function deliveryExplanation(status: string | null, detail: string | null): string | null {
  if (status === 'FAILED') {
    const why = detail ? capitalise(detail) : 'It could not be delivered.'
    return why + ' It did not arrive — you can retry it.'
  }
  if (status === 'REFUSED') return detail ? capitalise(detail) : 'The phone system refused it.'
  return detail ? capitalise(detail) : null
}

/** Is "Retry" offered under this bubble? An outbound text that FAILED, with its words. */
export function canRetry(e: DeliveryFacts): boolean {
  return e.direction === 'OUTBOUND' && e.type === 'SMS' && e.delivery_status === 'FAILED'
    && !!(e.body ?? '').trim()
}
