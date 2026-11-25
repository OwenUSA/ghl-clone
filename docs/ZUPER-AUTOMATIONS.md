# Everything that runs against Zuper by itself — inventory (re-checked 2026-11-25)

One page listing every scheduled job, container, background thread and in-Zuper workflow that
reads or writes Dream Team Roofing's Zuper account, where it lives, whether it works, and how to
pause it. First verified 2026-10-08; **every State below was re-read on 2026-11-25** (read-only:
cron, containers, logs, `.env.prod` switches, the CRM database, Zuper's API) unless it says
otherwise. The rules that govern all of it are in `ZUPER-OPERATIONS.md` section 1; the detail of
what each one does is in that file's section 4. This file is the index and the health check.

**Check it all at once (read-only, prints no customer data):**

```bash
bash ops/zuper-automations-status.sh
```

Since 2026-11-25 it also compares the safety-gate copies (section 4) and says when a container's
latest runs were refused by its gate.

---

## 1. The list

| # | What | Server | How it runs | Every | Writes to Zuper | State 2026-11-25 |
|---|---|---|---|---|---|---|
| A | **Technician routes** (`routes_sync.py`) | dispatch | cron `/etc/cron.d/zuper-routes` → `/opt/ops/zuper/zbrowser/routes_sync.sh` (moved from `/home/qa/zbrowser/` on 2026-11-06) | 1 min (full run every 5) | routes; toggles `jobs.notify_user_on_assignment` while writing | **Working.** Gate passing, last run 13:30 ET. AHS Simple Flow is a route board since 2026-11-06. State files in `/opt/ops/zuper/state/` (no longer `/tmp`) |
| B | **Checklist tasks** (`tasks_sync.py`) | owen-main | container `zuper_tasks`, `/opt/santiagoproperties/zuper-tasks` | 2 min | service tasks on AHS jobs | **STOPPED BY ITS GATE since 2026-10-31 11:41 ET** — every run refused (section 4). Last change written 2026-10-30. Container is up |
| C | **Proposal PDFs** (`compare_sync.py`) | owen-main | container `zuper_proposals`, `/opt/santiagoproperties/zuper-proposals` | 60 s | one attachment on each DRAFT proposal | **STOPPED BY ITS GATE since 2026-10-31 11:42 ET** — no `Proposal-DTR-N.pdf` for any draft made since. Last rebuild DTR-138, 2026-10-30. Container is up |
| D | **CRM mirror sweep** (`app/zuper/sweep.py`) | owen-main | `ghl_clone_worker`, Zuper thread | 15 min | none (`ZUPER_PULL_ONLY=true`) | **Working**, last success 18:22 UTC. No webhook has ever arrived. Mirrors AHS - Inspection, Retail, AHS - Repair & Review and the regional boards; **AHS Simple Flow is not mirrored** (not in `mapping.CATEGORIES` / `MIRROR_ONLY_CATEGORIES`) |
| E | **AHS email → Zuper job** (`engine.send` in `ahs_create` scope) | owen-main | owen-main mail reader (`CRM_LINK_EMAIL_JOBS_ENABLED=true`) → `POST /api/ahs-jobs` → queued `zuper_send` | per email | customer + job + note (3 POSTs) | **Working.** 19 done, 0 failed in the last 7 days. Since 2026-11-06 the job is made on **AHS Simple Flow** ("Got a job from AHS", `ZUPER_AHS_EMAIL_BOARD`); since 2026-11-25 it carries the board's task list (`ZUPER_AHS_EMAIL_TASKS=true`; the first real job after the deploy is not verified yet). The note is sent flat-body-first — see `ZUPER-OPERATIONS.md` 10 |
| F | **Signed proposal → job line items** (`app/zuper/proposals.py`) | owen-main | `ghl_clone_worker`, after each sweep (`ZUPER_PROPOSAL_LINES=true`) | 15 min | one `PUT /jobs` (products + job_total) | **On.** 121 proposals handled |
| G | **Dispatch page pass** (`app/dispatch/service.py`) | owen-main | `ghl_clone_worker`, Zuper thread (`DISPATCH_ENABLED=true`) | 150 s | none; phase-3 writes OFF (`DISPATCH_ZUPER_WRITES=false`) | **Working**, last pass 18:24 UTC. Until 2026-11-25 it listed all of Simple Flow's columns as stages it did not know (fixed with the "Special Cases" column) |
| H | **KPI history + nightly Excel** (`app/zuper/history.py`, `app/kpi_report.py`) | owen-main | `ghl_clone_worker`, after 02:00 ET | daily | none | **Working.** `KPI-2026-11-25.xlsx` written |
| I | **Daily activity digest** (`app/zuper/digest.py`) | owen-main | `ghl_clone_worker`, after 01:00 ET | daily | would add a private note | **Runs, writes nothing**: pull-only refuses the note. 505 attempts, 0 notes. Dead code under the mirror |
| J | **CRM appointment reminder texts** (`app/reminders/`) | owen-main | `ghl_clone_worker` | 60 s | none — texts through owen-main | **OFF since 2026-10-12** (`ZUPER_REMINDERS_ENABLED=false`, every mode Off). Code and tables kept |
| K | **Seven DTR customer-text workflows** (Zuper Workflow Builder) | inside Zuper | Zuper | per schedule / column move / incoming text | none (sent texts) | **ALL SEVEN OFF since 2026-10-26** (user stopped every automated customer message). Live 2026-10-13 → 10-26 (the welcome text from 10-22). Record: `ZUPER-CUSTOMER-TEXT-WORKFLOWS.md` |
| L | **CRM auto-reply / dropped-call text** (`app/autoreply/`, `app/dropped_call.py`) | owen-main | `ghl_clone_worker` | 60 s | one `POST /customers/{uid}/send` + one private note | **OFF since 2026-10-26** (`ZUPER_AUTOREPLY_ENABLED=false`, `DROPPED_CALL_TEXTS_ENABLED=false`) |
| M | **Six team workflows** (Zuper Workflow Builder, made by the team or Zuper's templates — not ours) | inside Zuper | Zuper | on their events | **yes — Zuper's own writes**: job status, appointments, invoices, line items | **ACTIVE**, all ran on 2026-11-25 except two (last 2026-11-02). The gate allows them only while none has a messaging step (`TEAM_WORKFLOWS`, 2026-11-06). See the list below |
| N | **Owen AI** (`app/owen_ai/`) | owen-main | `ghl_clone_api` / `ghl_clone_worker`, only when Owen asks in the chat | per request | only on Owen's own approval: a DRAFT proposal, a new customer + job, a report PDF on a draft, the changes he says "do it" to (column on the job's own board, visit, technicians, a field, addresses, a private note, pictures added to the gallery) | **On** (`OWEN_AI_PROPOSALS_ENABLED`, `OWEN_AI_LEADS_ENABLED`, `OWEN_AI_REPORT_ATTACH_ENABLED`, `OWEN_AI_ZUPER_CHANGES_ENABLED` = true). Each write has its own scope in `client.py` (`owen_ai_*`) |

**The six team workflows (M), checked 2026-11-25:**

| Workflow | Made by | Starts on | Does (by its name; steps not read) |
|---|---|---|---|
| Add Appointments for Inspection and Production | Zuper Admin, 2026-11-05 | a move into Simple Flow "Got a job from AHS" or "Handoff Meeting" | adds appointments (the 3 "Confirm inspection…" tasks on some jobs come from here) |
| Ready to Invoice Job Status update to New Invoice creation | Owen, 2026-11-01 | a move into "Ready to Invoice" | creates an invoice |
| Add line items from an accepted quote to Job | Zuper template, 2026-09-10 | a quote's status change | copies the accepted quote's lines to the job |
| RG3-T55 - Upon job user assignment, update job status | Zuper template | a user assigned to a job | changes the job's column |
| RG3-T55 - Upon Job schedule, update job status | Zuper template | a job scheduled | changes the job's column |
| Create AHS Repair on estimate acceptance | Zuper template | a move into "Won - TBD" / "Estimate Accepted" | creates an AHS repair job |

So a job's column can change in Zuper with nobody moving it: when it is scheduled or assigned.

Nothing else touches Zuper. Checked and **not** a Zuper writer: the CRM's payments pass
(`PAYMENTS_ENABLED=true`, reads only — its reminder text needs `PAYMENTS_TEXTS_ENABLED`, unset),
inspection docs / tech briefing / weather / insights (read Zuper only, or not at all),
follow-ups, reviews and the call follow-up (their server switches are unset), every other cron on
both boxes, `callmon_*` / `owen_voice`.

## 2. Switched off, kept for the record

| What | Where | Off since | Backup |
|---|---|---|---|
| The seven DTR customer-text workflows (K) | inside Zuper, inactive | 2026-10-26 | `workflows_before_stop_all_20261026-220947.json` in owen-main `zuper-backup/before-changes/2026-10-26/` |
| CRM reminders, auto-reply, dropped-call text (J, L) | `.env.prod` | 2026-10-12 / 2026-10-26 | `.env.prod.bak-20261026-stop-texts` |
| AHS - TEST column tasks (`ahstest/column_tasks.sh`) | dispatch cron | 2026-09-27 | `/root/backups/cron.d-zuper-ahstest-tasks.disabled-20260927` (cron copies stayed on dispatch) |
| Proposal PDFs on dispatch (old home of C) | dispatch cron `zuper-compare` | 2026-09-24 (moved to owen-main) | `/root/backups/cron.d-zuper-compare.disabled-20260924` |
| Approved → Repair & Review hand-off (inside B) | `tasks_sync.py` | 2026-09-30, owner: keep it off | `app/tasks_sync.py.bak-20260930` |
| Every other Zuper workflow (51 in the account on 2026-11-25; only M's six are active) + 21 customer notification rules | inside Zuper | 2026-09-17 | checked every minute by the gate |

## 3. How to pause each one

| # | Pause | Resume |
|---|---|---|
| A | comment out the line in `/etc/cron.d/zuper-routes` | uncomment it. **Never run a second copy** |
| B | `cd /opt/santiagoproperties/zuper-tasks && docker compose stop` (do this before changing any checklist or stage) | `docker compose up -d --force-recreate` |
| C | `cd /opt/santiagoproperties/zuper-proposals && docker compose stop` | same as B |
| D–I | `ZUPER_SYNC_ENABLED=false` in `ghl-clone/.env.prod` stops all of them; or Settings → Zuper switch | recreate `api` + `worker` |
| E | `CRM_LINK_EMAIL_JOBS_ENABLED=false` on owen-main, or `ZUPER_AHS_EMAIL_CREATES_JOBS=false` in the CRM; `ZUPER_AHS_EMAIL_TASKS=false` drops only the task list | |
| F | `ZUPER_PROPOSAL_LINES=false` | |
| G | `DISPATCH_ENABLED=false` | |
| K, M | Zuper → Workflows → switch it off (`PUT {workflow host}/workflows/{uid}/deactivate`). M is the team's — ask the owner first | switch it on again; a K workflow must be on `APPROVED_WORKFLOWS` |
| N | the `OWEN_AI_*_ENABLED` switch of that write, or `OWEN_AI_ENABLED=false` | |

After changing a container's code: `docker compose stop` → `build` → `up -d --force-recreate`,
then `docker exec <name> grep …` to prove the new code is inside. `docker start` runs the OLD
container (2026-09-25: 94 duplicate tasks).

## 4. Every writer goes through the safety gate

A, B and C run `zsafety.py` first and write nothing when it fails (log line `SAFETY GATE FAILED`).
The gate fails when any Zuper workflow not on its lists, customer notification rule,
customer-facing job alert or reminder, or company-config messaging switch is active — or when
Zuper itself errors. Since 2026-11-06 dispatch's copy also allows the six team workflows
(`TEAM_WORKFLOWS`, only while none has a messaging step) and four switches the team turned on
(`KNOWN_OK`: `estimate/invoice.secondary_customers.notify`, `estimate/invoice.enable_payment_link`).
D–I and N are inside the CRM and are fenced by `app/zuper/client.py` instead (denylist,
pull-only, the narrow write scopes).

There are three copies of `zsafety.py`: dispatch `/opt/ops/zuper/zsafety.py` (moved from
`/home/qa/` on 2026-11-06), and `app/zsafety.py` in each owen-main container. **Change all three
together.**

> **They differ, and that has stopped B and C (2026-11-25).** The two owen-main copies (dated
> 2026-10-22, identical to each other) lack `TEAM_WORKFLOWS` and `KNOWN_OK`. The team's workflows
> went active on 2026-10-31, so since 11:41 ET that day every run of B and C says `SAFETY GATE
> FAILED` and writes nothing; the routes job (A) kept running because only dispatch's copy was
> updated on 2026-11-06. Fix: copy dispatch's file into both `app/` folders and recreate the two
> containers — **owner's OK first** (a server change; C will then build PDFs for every draft made
> since 2026-10-31, including Owen AI's).

## 5. Known loose ends (2026-11-25)

1. **B and C are stopped by the old gate copy** (section 4).
2. **The code for A, B and C is in no git repository.** It exists only on the servers
   (`/opt/ops/zuper/` on dispatch; `app/` in each owen-main folder), with `*.bak-<date>` copies.
3. The digest (I) tries every night and is refused; switch it off in code or leave it.
4. Webhooks: none registered for the CRM, so changes arrive within 15 min, not instantly
   (`webhook.EVENTS` still has the wrong event names — the real ones are dotted lowercase,
   `job.status_update`).
5. AHS Simple Flow is not mirrored into the CRM (D). Dispatch, the planner, Owen AI and the
   routes read it directly.
6. `zuper_tasks` still has `TASKS_EXTRA_JOBS` = test job #694, deleted 2026-09-28. Harmless.
