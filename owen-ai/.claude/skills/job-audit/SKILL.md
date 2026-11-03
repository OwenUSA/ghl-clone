---
name: job-audit
description: Audit one AHS Simple Flow job against the playbook and record every process failure as an alert (customer not called, visit not confirmed the day before, proposal not sent or not followed up, deposit missing, quality call not made, promise not kept, hold nobody is working...). Use for scheduled audits, and when Owen asks "is anything wrong with #N", "audit the board", "qué falta en este trabajo".
---

# Job audit

The rules are in `knowledge/ahs-simple-flow-playbook.md` (§ audit rules table, § holds, § evidence
rules). Read the parts you need with Grep/Read — at least the rules for the job's current stage,
the holds section and the evidence rules — before judging.

## Steps

1. `mcp__owen__job_dossier` with the job number or uid. It returns the job (stage, how long in it,
   history, visits, checklist answers, line items, notes), its proposals, invoices, payments and
   the merged Zuper Connect + Quo conversation timeline. Do NOT pull raw Zuper unless the dossier
   says a source is missing and you truly need it.
2. `mcp__owen__open_issues` with the job_uid: the alerts already open for it.
3. Walk the job's lifecycle up to its current stage. For every rule that applies, decide:
   **fail** (evidence shows it did not happen / is overdue), **ok** (evidence shows it happened),
   or **can't verify** (the data needed is not available). Only *fail* becomes an alert.
4. Read every message and call summary in the timeline for:
   - a promise from our side ("we'll send…", "Antonio will come back Tuesday") — is it kept by its date?
   - a price or scope agreed with a technician — is it in Zuper (line items / proposal)?
   - a customer question or request nobody answered;
   - frustration, complaint, "still leaking", threat to cancel or review.
5. For each failure: `mcp__owen__report_issue` with the playbook rule_id (or `SF-CHAT-*` /
   `SF-OTHER` if no rule fits), severity from the playbook, a short title ("Day-before
   confirmation missing for Wed 11/5 repair"), the evidence (dates, channel, who, exact words)
   and the one action to take.
6. For each open alert that is now fixed: `mcp__owen__clear_issue` with what fixed it.
   (Alerts you neither raise nor clear are resolved automatically after a scheduled audit.)

## Don'ts

- No alert without evidence. Missing data = "can't verify", never a failure.
- Respect business hours (Mon–Sat 8 AM–6 PM ET) when counting deadlines.
- Job #701 / customer Santiago Villahermosa is the TEST job: audit it normally, but say "TEST"
  in every title.
- Jobs migrated from the old AHS boards: steps done before 2026-10-31 may only show in the old
  board's history or in the chats — look there before calling a step missing.
- You cannot change anything in Zuper or contact anyone. Say what should be done.

## Answer (short)

```
#<n> · <stage> (<days> d) — <N> issues (<urgent>/<today>/<watch>), <M> cleared
- [urgent] <title> — <action>
- ...
Can't verify: <rule ids and why, one line>
```
