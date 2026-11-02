---
name: daily-briefing
description: Owen's morning briefing — what happened yesterday in Zuper and Quo, open promises and follow-ups, prices agreed, and today's jobs. Use when Owen says "briefing", "morning report", "what's going on today", "resumen del día".
---

# Daily briefing

Florida time. "Yesterday" = the previous business day (Monday covers Fri–Sun).

1. **Today's jobs** — Zuper jobs scheduled today (`GET /jobs` with a scheduled-date filter; see
   knowledge/zuper/api-reference.md §1 for the filter params that work). Per job: #, customer
   first name, board + column, technician, time window.
2. **Yesterday's movement** — jobs that changed column (`GET /activities/recent`), proposals
   created/approved/declined (`GET /estimate`, newest first), invoices/payments.
3. **Conversations** — Quo calls (missed ones first) and texts since yesterday, and Zuper Connect
   conversations. For each that matters: who, what they want, whether we answered.
4. **Commitments found** — promises made to customers, prices agreed with a technician,
   follow-ups owed, info a tech needs. Quote the exact words, date and source.
5. **Proposals waiting** — proposals sent and not answered for 3+ days (age, amount, job #).
6. **AHS Simple Flow board** — count per column; jobs stuck in a column > 3 days.

Output:

```
## Briefing — <Day Mon D>
**Today:** N jobs · N proposals waiting · N missed calls · N open promises
### Do first            (max 5 bullets, most urgent first)
### Today's jobs        (table)
### Promises & follow-ups (table: customer/job · what was promised · when · source)
### Prices agreed        (table: job · amount · who agreed · source) — flag any not in Zuper
### Proposals waiting    (table)
### Board                (AHS Simple Flow counts, stuck jobs)
```

Skip a section that is empty (say "none"). Never invent a number.
