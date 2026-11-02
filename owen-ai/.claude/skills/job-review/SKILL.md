---
name: job-review
description: Full status of one Zuper job — where it is, what was said, what was promised and quoted, what is next. Use for "how is job #701", "review this job", "qué pasa con el trabajo de X", and at the start of a Job conversation.
---

# Job review

1. Find the job (`GET /jobs?filter.keyword=<number>`; confirm the number). Then `GET /jobs/{uid}`.
2. Read: board (category) + column (current status) + when it moved there, customer, address,
   technician(s), scheduled visit, custom fields / checklist answers that are filled, line items.
3. Notes: `GET /jobs/{uid}/note` (if the note text is missing, say so — known gap).
4. Proposals for the job (`GET /estimate` filtered to the job/customer): number, status,
   options with prices, which option was approved.
5. Invoices and payments for the job.
6. Conversations with the customer's phone number(s): Quo calls (summaries/transcripts) and
   texts, Zuper Connect texts/calls — last 30 days.
7. Extract commitments: promises, prices agreed, follow-ups owed, tech info.

Output:

```
## #<n> — <customer first name>, <city>  ·  <board> → <column> (since <date>)
**Next step:** <one line>
### Money       proposal(s) / approved option / invoice / paid
### Timeline    (date · event · source) — newest first, max 15
### Promises & open items
### For the technician
```
