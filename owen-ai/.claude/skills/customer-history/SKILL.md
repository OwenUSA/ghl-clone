---
name: customer-history
description: Everything we have with one customer across Zuper and Quo — jobs, proposals, invoices, calls, texts, and every promise or price mentioned. Use for "history of <customer>", "what did we talk with <name>", "historial del cliente".
---

# Customer history

1. Find the customer in Zuper (`GET /customers` search). If several match, list and ask.
2. Their jobs (all boards), proposals, invoices, payments.
3. Their phone numbers → Quo: for each of our Quo numbers, calls and messages with those
   participants; summaries/transcripts for calls that matter. Zuper Connect conversations too.
4. Build one timeline (newest first) and pull out promises, prices and open items.

Output: a short header (name, phone, jobs count, total quoted / approved / paid), then
**Open items**, then the **Timeline** table (date · channel · what happened · source).
