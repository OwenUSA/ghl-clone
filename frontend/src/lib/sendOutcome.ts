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
 *   "failed: <why>"     we could not reach the phone system
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

  if (reason.startsWith('failed: '))
    return 'Not sent. ' + capitalise(reason.slice('failed: '.length))

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
