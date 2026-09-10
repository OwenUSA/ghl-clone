/**
 * What to tell the user happened to an appointment's reminders.
 *
 * Import-free on purpose, like `calendarGrid.ts`, so node can execute it
 * directly and `backend/tests/test_reminder_sentence.py` can assert the real
 * mapping rather than pattern-match the source. This project's usual frontend
 * idiom is "assert against source", and that is too weak here: the whole point
 * of this text is that a reminder which silently did not move must not be
 * reported as one that did, and a regex saying "the file mentions rescheduled"
 * would pass against wording that says the opposite.
 *
 * The input is the backend's own `automation` outcome string from
 * `PATCH /api/appointments/{id}` (see `on_appointment_booked` and
 * `update_appointment` in backend/app/main.py). It is translated rather than
 * printed: "queued 24h,1h" is a queue detail, and what a dispatcher needs to
 * know is whether the customer will still be told the crew is coming.
 */
export function reminderSentence(automation: string): string {
  if (automation === 'unchanged') return 'Saved.'
  if (automation === 'reminders cancelled')
    return 'Saved. Any pending reminder has been withdrawn.'
  if (automation.startsWith('queued ')) {
    const offsets = automation
      .slice('queued '.length)
      .split(',')
      .map((o) => (o === '24h' ? '24 hours' : o === '1h' ? '1 hour' : o))
    return 'Saved and rescheduled. The customer will be reminded '
      + offsets.join(' and ') + ' before the new time.'
  }
  // A move into the next hour leaves nothing that can fire in the future, and a
  // reminder scheduled in the past would go out immediately — which is worse
  // than not sending. Say so; silence here reads as "the reminder is handled".
  if (automation === 'nothing to schedule')
    return 'Saved and rescheduled. The new time is too soon to send a reminder, '
      + 'so none was queued — tell the customer yourself.'
  // DND, or a contact with no phone number, or no contact at all. The booking is
  // saved either way; the customer just will not hear about it from us.
  if (automation.startsWith('suppressed: '))
    return 'Saved and rescheduled, but no reminder was queued: '
      + automation.slice('suppressed: '.length) + '.'
  if (automation.startsWith('no reminders for '))
    return 'Saved. ' + automation.charAt(0).toUpperCase() + automation.slice(1) + '.'
  // An outcome this function has not been taught. Show it rather than swallow
  // it: an unrecognised reminder outcome reported as plain "Saved." is the exact
  // silence this whole surface was deferred over.
  return 'Saved. Reminders: ' + automation + '.'
}
