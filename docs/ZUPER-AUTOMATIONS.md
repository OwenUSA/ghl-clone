# Everything that runs against Zuper by itself — inventory (verified 2026-10-08, re-checked 2026-10-12; K added 2026-10-13)

One page listing every scheduled job, container and background thread that reads or writes
Dream Team Roofing's Zuper account, where it lives, whether it works, and how to pause it.
Checked on both servers on 2026-10-08 and again on 2026-10-12 (read-only: cron, containers, logs,
the CRM database, and Zuper's API). The **State** column is 2026-10-08 unless it says otherwise.
The rules that govern all of it are in `ZUPER-OPERATIONS.md` section 1; the detail of what each
one does is in that file's section 4. This file is the index and the health check.

**Check it all at once (read-only, prints no customer data):**

```bash
bash ops/zuper-automations-status.sh
```

---

## 1. The list

| # | What | Server | How it runs | Every | Writes to Zuper | State 2026-10-08 |
|---|---|---|---|---|---|---|
| A | **Technician routes** (`routes_sync.py`) | dispatch | cron `/etc/cron.d/zuper-routes` → `/home/qa/zbrowser/routes_sync.sh` | 1 min (full run every 5) | routes; toggles `jobs.notify_user_on_assignment` while writing | **Working.** Gate passing, 0 tracebacks in the recent log, 7 WARN lines (jobs with no time / no technician) |
| B | **Checklist tasks** (`tasks_sync.py`) | owen-main | container `zuper_tasks`, `/opt/santiagoproperties/zuper-tasks` | 2 min | service tasks on AHS jobs | **Working.** Last change 2026-10-08 10:30 ET. Occasional Zuper 502s, retried next loop |
| C | **Proposal PDFs** (`compare_sync.py`) | owen-main | container `zuper_proposals`, `/opt/santiagoproperties/zuper-proposals` | 60 s | one attachment on each DRAFT proposal | **Working.** Last rebuild DTR-43, 2026-10-08 13:57 ET. Occasional `IncompleteRead` on Zuper's 17 MB PDF, retried |
| D | **CRM mirror sweep** (`app/zuper/sweep.py`) | owen-main | `ghl_clone_worker`, Zuper thread | 15 min | none (`ZUPER_PULL_ONLY=true`) | **Working** (again 2026-10-12). **No webhook has ever arrived** — the sweep is the only feed. **The CRM still has the old AHS pipeline** — `mirror --phase boards` was never committed (`ZUPER-OPERATIONS.md` 4.4) |
| E | **AHS email → Zuper job** (`engine.send` in `ahs_email` scope) | owen-main | owen-main mail reader (`CRM_LINK_EMAIL_JOBS_ENABLED=true`) → `POST /api/ahs-jobs` → queued `zuper_send` | per email | customer + job + note (3 POSTs) | **Working.** 7 done, 1 failed in the last 7 days ("Customer Already Exists", 2026-10-03 — that work order needs a person to check that its job exists in Zuper). 2026-10-12: 6 done, 0 failed in the last 7 days |
| F | **Signed proposal → job line items** (`app/zuper/proposals.py`) | owen-main | `ghl_clone_worker`, after each sweep (`ZUPER_PROPOSAL_LINES=true`) | 15 min | one `PUT /jobs` (products + job_total) | **Working.** 4 proposals handled; last 2026-10-08 |
| G | **Dispatch page pass** (`app/dispatch/service.py`) | owen-main | `ghl_clone_worker`, Zuper thread (`DISPATCH_ENABLED=true`) | 150 s | none; phase-3 writes OFF (`DISPATCH_ZUPER_WRITES` unset) | **Working.** Last pass 19:29 UTC, no error. Reports one stage it does not know: `AHS - TEST / Intake` |
| H | **KPI history + nightly Excel** (`app/zuper/history.py`, `app/kpi_report.py`) | owen-main | `ghl_clone_worker`, after 02:00 ET | daily | none | **Working.** `KPI-2026-10-08.xlsx` written |
| J | **Appointment reminder texts** (`app/reminders/`) | owen-main | `ghl_clone_worker`, after each Dispatch pass (`ZUPER_REMINDERS_ENABLED` + Settings → Automations mode) | 60 s | none — reads the Dispatch copy; texts customers through owen-main | **OFF since 2026-10-12** (user: "i dont want them anymore"): `ZUPER_REMINDERS_ENABLED=false` and every mode Off. Built 2026-10-08; also the "report submitted to AHS" and "AHS authorized" texts (2026-10-09, `stage_texts.py`). Code and tables kept |
| K | **Six customer-text workflows** (Zuper Workflow Builder) | inside Zuper | Zuper runs them; incoming texts arrive by Zuper webhook `sms.new_sms` | per schedule / column move / incoming text | none (no job writes) | **LIVE since 2026-10-13 16:41 ET** (user: "ok activate"). Text AHS customers from (954) 420-7373: day-before and 2-hour reminders, Inspection Completed / Awaiting AHS Decision / AHS Approved texts, thank-yous. Bell alerts in them fail on Zuper's side. Everything: `ZUPER-CUSTOMER-TEXT-WORKFLOWS.md` |
| L | **Auto-reply to unanswered texts / missed calls** (`app/autoreply/`) | owen-main | `ghl_clone_worker`, its own thread (`ZUPER_AUTOREPLY_ENABLED` + Dispatch → Settings switch) | 60 s | one `POST /customers/{uid}/send` (a text from (954) 420-7373) + one private job note per unanswered stretch | **Built 2026-10-19, OFF**; test mode (TEST customer only) until an ADMIN types TURN ON. 30/day, 1 per number per 24 h. `DECISIONS.md` 2026-10-19 |
| I | **Daily activity digest** (`app/zuper/digest.py`) | owen-main | `ghl_clone_worker`, after 01:00 ET | daily | would add a private note | **Runs, writes nothing**: pull-only refuses the note. 156 attempts, 0 notes (2026-10-12). Harmless, but dead code under the mirror |

Nothing else touches Zuper. Checked and **not** a Zuper automation: every other cron on both
boxes (backups, Traefik sync, sysstat), `callmon_*` / `owen_voice` (owen-main reads Zuper data
only through the CRM, for the Retell caller brief), the tmux sessions on dispatch (agent hosts,
no Zuper process running).

## 2. Switched off, kept for the record

| What | Where | Off since | Backup of the schedule |
|---|---|---|---|
| AHS - TEST column tasks (`ahstest/column_tasks.sh`) | dispatch cron | 2026-09-27 | `/root/backups/cron.d-zuper-ahstest-tasks.disabled-20260927` |
| Proposal PDFs on dispatch (old home of C) | dispatch cron `zuper-compare` | 2026-09-24 (moved to owen-main) | `/root/backups/cron.d-zuper-compare.disabled-20260924` |
| Approved → Repair & Review hand-off (inside B) | `tasks_sync.py` | 2026-09-30, owner: keep it off | `app/tasks_sync.py.bak-20260930` |
| The two September "DTR - Appointment reminder" workflow drafts | inside Zuper, INACTIVE | built inactive 2026-09-21; replaced by K | — |
| The other 41 Zuper workflows (not K) + 21 customer notification rules | inside Zuper | 2026-09-17 | checked every minute by the gate (it allows only K's six uids) |

## 3. How to pause each one

| # | Pause | Resume |
|---|---|---|
| A | comment out the line in `/etc/cron.d/zuper-routes` | uncomment it. **Never run a second copy** |
| B | `cd /opt/santiagoproperties/zuper-tasks && docker compose stop` (do this before changing any checklist or stage) | `docker compose up -d --force-recreate` |
| C | `cd /opt/santiagoproperties/zuper-proposals && docker compose stop` | same as B |
| D–I | `ZUPER_SYNC_ENABLED=false` in `ghl-clone/.env.prod` stops all of them; or Settings → Zuper switch | recreate `api` + `worker` |
| E | `CRM_LINK_EMAIL_JOBS_ENABLED=false` on owen-main, or `ZUPER_AHS_EMAIL_CREATES_JOBS=false` in the CRM | |
| F | `ZUPER_PROPOSAL_LINES=false` | |
| G | `DISPATCH_ENABLED=false` | |
| J | Settings → Automations → Appointment reminders → **Off** (one press), or `ZUPER_REMINDERS_ENABLED=false` | Test / On with the typed "TURN ON" |
| K | Zuper → Workflows → switch each of the six off (`ZUPER-CUSTOMER-TEXT-WORKFLOWS.md` section 8) | switch them on again; the gate allows only these six |

After changing a container's code: `docker compose stop` → `build` → `up -d --force-recreate`,
then `docker exec <name> grep …` to prove the new code is inside. `docker start` runs the OLD
container (2026-09-25: 94 duplicate tasks).

## 4. Every writer goes through the safety gate

A, B and C run `zsafety.py` first and write nothing when it fails (log line `SAFETY GATE FAILED`).
The gate fails when any Zuper workflow, customer notification rule, customer-facing job alert or
reminder, or company-config messaging switch is active — or when Zuper itself errors (the only
failure in the recent routes log was a Zuper 502 on 2026-10-06). D–I are inside the CRM and are
fenced by `app/zuper/client.py` instead (denylist, pull-only, the narrow write scopes).
There are three copies of `zsafety.py`: dispatch `/home/qa/zsafety.py`, and `app/zsafety.py` in
each owen-main container. **Change all three together.**

> **They differ today (2026-10-08).** The two owen-main copies are identical to each other but
> OLDER than dispatch's: they lack the 2026-09-26 check of job status alerts, delay alerts and
> reminders (an active one that is SMS / EMAIL or names a customer). So B and C would still write
> while such an alert is active; only A would stop. Fix: copy dispatch's file into both
> `app/` folders and recreate the two containers (owner's OK first — it is a server change).

## 5. Known loose ends (2026-10-08, re-checked 2026-10-12)

0. **The owen-main containers run the older safety gate** (section 4). Still true 2026-10-12
   (dispatch's file and the two container copies differ).
1. **The code for A, B and C is in no git repository.** It exists only on the servers, with
   `*.bak-<date>` copies beside it. dispatch `/home/qa/zbrowser/` holds 136 scripts, most of them
   one-off probes; the live ones are `routes_sync.py`, `routes_sync.sh`, `assign_sync.py`,
   `zb_common.py`, plus `/home/qa/zsafety.py`.
2. `zuper_tasks` still has `TASKS_EXTRA_JOBS` = test job #694, deleted 2026-09-28. Harmless; drop
   it at the next rebuild.
3. One AHS email card failed to reach Zuper on 2026-10-03 ("Customer Already Exists").
7. The CRM mirror has never applied the current boards: no AHS - Repair & Review or regional
   pipelines, and the AHS pipeline has the pre-2026-09-25 stages (`ZUPER-OPERATIONS.md` 4.4).
4. The digest (I) tries every night and is refused; switch it off in code or leave it.
5. Dispatch does not know the `AHS - TEST / Intake` stage (test board).
6. Webhooks: none registered for the CRM, so changes arrive within 15 min, not instantly
   (`webhook.EVENTS` still has the wrong event names — see the memory note on webhooks).
