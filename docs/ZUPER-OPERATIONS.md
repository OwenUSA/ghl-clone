# Zuper operations — handoff (validated against live Zuper 2026-10-12)

Everything a new session needs to manage Dream Team Roofing's **Zuper** account: the rules, how to
reach it, what is set up (the feature map), what runs on its own, and what is still open.
Nothing secret is in this file — only *where* things are. Never print a key, a password or a
customer's data.

**Last validated 2026-10-12**, read-only: categories, columns and their Status Types, job counts,
stage checklists, job fields, webhooks and company config read from the API (GET only, from
dispatch); cron, containers, logs and the CRM database read on both servers
(`bash ops/zuper-automations-status.sh`). Sections marked *(2026-10-12)* were corrected then.
The index of everything that runs by itself is `ZUPER-AUTOMATIONS.md`.

The running log of every change (with backups and exact API bodies) is the memory note
`zuper-automation-off.md` in the Claude memory folder for this project. This file is the
organised summary.

---

## 1. The owner's rules (non-negotiable)

1. **Never send a customer anything automatically.** On 2026-09-17 a customer load triggered
   Zuper's template workflows and 231 customers got a mistaken email/SMS. Since then all 41
   workflows and all 21 customer-notification rules are **inactive**, and the owner re-enables
   automations himself, one at a time. (Still 0 of 41 and 0 of 21 active on 2026-10-12.)
   **Exception since 2026-10-13 16:41 New York:** six DTR workflows that text AHS customers
   (reminders, three column texts, thank-yous) are LIVE at the user's request — the only
   automatic customer messaging in the account. Section 4.5 and `ZUPER-CUSTOMER-TEXT-WORKFLOWS.md`.
   The other 41 workflows and all 21 notification rules are still off.
   The CRM's own automatic texts read from Zuper columns (appointment reminders, "report
   submitted to AHS", "AHS authorized", 2026-10-08/09) were **switched off completely on
   2026-10-12** at the user's request: `ZUPER_REMINDERS_ENABLED=false` and every mode Off. The
   code stays. Do not switch them back on unless asked.
2. **Run the safety gate before every write:** `python3 /home/qa/zsafety.py` on dispatch
   (exit 0 = safe). It checks workflows, notification rules and the company-config switches.
   Every script and both containers below refuse to write when it fails.
3. **Never delete a job** — jobs carry photos. Move them (same job) instead. The only jobs ever
   deleted were test jobs created by us (#671 on 2026-09-22, #697 a minute after creating it).
4. **Back up before changing anything** (`/root/backups/` on dispatch), read back after, and
   compare. Test on the test job first (section 7).
5. **Zuper is where the team works now.** The CRM only *copies* Zuper (pull-only). An older
   Workiz export must never move a job backwards (the apply script enforces it, section 6).
6. **Read-only by default (boss's rule, 2026-09-27):** "Zuper must work on its own, the only
   thing we can do is read from Zuper." Nothing new may write to Zuper without the owner lifting
   the rule for that one thing. Lifts granted so far: AHS work-order emails create their job
   (2026-09-28, section 4), `messaging.notify_users` set to Owen + Luis (2026-09-28), two saved
   board views (2026-09-30), the KPI questions and their job fields (2026-09-30,
   `ZUPER-KPI-SETUP.md`), the inspection questions (2026-10-12, 3.3a), the AHS - Inspection
   column order (2026-10-12), and the six customer-text workflows plus the System Notification
   Number 19544207373 (2026-10-13, 4.5). Each lift covers that one thing only. The jobs in section 4 that write (`zuper_tasks`, `zuper_proposals`,
   routes) predate the rule; the owner said "ok leave that" on 2026-09-28.

---

## 2. Access

| What | Where |
|---|---|
| API base | `https://us-east-1.zuperpro.com/api`, header `x-api-key` |
| API key (dispatch, for scripts) | `/home/qa/.zuper.key` and `/root/secrets/zuper.key` (0600) |
| API key (owen-main containers) | `/opt/santiagoproperties/zuper-proposals/secrets/zuper.key` (0600, mounted read-only into both containers) |
| API key (CRM) | `ZUPER_API_KEY` in `/opt/santiagoproperties/ghl-clone/.env.prod` on owen-main |
| Workflow host (workflows are here, not under /api) | `https://us-east-1-workflow.zuperpro.com/api` |
| Web app | `https://web.zuperpro.com` |

**The key belongs to Owen's user**, so everything done through the API shows as "by Owen
Buzaglo" in Zuper (this confused a test once — say so when you change something by API).

### The headless browser (Playwright) session

| What | Where |
|---|---|
| Saved Zuper login | `/home/qa/zbrowser/zuper_state.json` (Playwright `storage_state`, 0600, the token is in web.zuperpro.com localStorage). **Verified working 2026-09-25** (a read-only load of /jobs stayed signed in). |
| Python + Playwright | `/home/qa/zbrowser/.venv` — run as the **qa** user (`su - qa`), the Chromium build is in qa's cache, not root's |
| Write guard for browser scripts | `/home/qa/zbrowser/zb_common.py` (`make_guard`): blocks every non-GET except an allow-list; always blocks workflow / notification / send / invite / company-config / custom_fields URLs; logs to `guard.log` |
| Captured web-app API calls | `/home/qa/zbrowser/probe_responses.json` |
| Example scripts | `board_probe*.py`, `card_*.py`, `zb_probe.py` (all load `zuper_state.json`) |

**The new UI (`next.zuperpro.com`) is mapped in `ZUPER-NEXT-UI-MAP.md`** (2026-10-05): every
module and settings page, the API behind each, and a separate read-only browser
(`/home/qa/zbrowser/nextmap/`, login `zuper_next_state.json`). The login above does not work there.

If the login ever expires, the owner has to sign in again in a headed browser and save a new
`storage_state` to the same path. Headless browsing needs `POST p.zuperpro.com/flags` allowed or
settings tables never render.

**Finding an undocumented endpoint:** download the web app bundles (`https://web.zuperpro.com/`
→ `main-*.js` → every `*-XXXXXXXX.js` chunk it names, 3–4 levels) and grep them. Every
endpoint in section 8 marked "(web JS)" was found this way and then tested on a test record.

---

## 3. Feature map — what is set up in Zuper

There was no separate "feature map" file; this section is it. Deeper research notes (how Zuper
models jobs, categories, statuses, projects; API facts) live on dispatch in
`/home/qa/refs/zuper/`: `zuper-research.md`, `zuper-job-organisation.md`, `zuper-checklist.md`,
`zuper-decisions-v2.txt`. Earlier build reports: `/home/qa/zsup/*.md` (`ZBUILD-RESULT.md`,
`ZMIG-RESULT.md`, `LOAD-PLAN.md`, `OWNER-TODO.md`).

### 3.1 Pipelines (Zuper "job categories"), in the order Zuper shows them *(2026-10-12)*

19 categories. Only three hold jobs.

| # | Pipeline | uid | Notes |
|---|---|---|---|
| 1 | **AHS - Inspection** | `f07e3c01-97b1-404d-8929-175845774a60` | the old "AHS", renamed 2026-09-25; ends at **AHS Approved** |
| 2 | **AHS - Repair & Review** | `2d0160ea-cf71-46f5-9911-e43d80c42d96` | new 2026-09-25 |
| 3 | **Retail** | `5939c976-002a-4d80-8bfd-2669a1f8843f` | |
| 4–10 | Lead Qualification, Roof Inspection, Repair Service (`e35524d8-6080-473a-b522-ce492b3735b1`), Production - Roof / Metal Roof / Gutters / Siding | — | Zuper template pipelines. **No jobs on any of them on 2026-10-12.** Repair Service is still a route board (in case the team uses it) |
| 11 | Miami Retail Repair | `520c342f-b1d6-4082-b047-2fd4039202c4` | Retail's 16 stages, no jobs yet |
| 12 | Miami Retail Roof Replacement | `bce5119a-58cd-46d9-8fa9-cb420eb8c940` | 〃 |
| 13 | Miami Gutters | `a29e16c7-590b-47ac-9527-162b71cdc488` | 〃 |
| 14 | Sarasota Repairs | `6af83eaa-50f0-426b-93ad-ee46afcd0b43` | 〃 |
| 15 | Sarasota Roof Replacement | `601fd5ee-5040-4d7f-8a9a-459e62e1a0e0` | 〃 |
| 16 | Sarasota Gutters | `fda33634-1655-45fb-8503-d5d3111745d6` | 〃 |
| 17 | Leak repair | `449461c8-4f24-411c-8215-004c7c94464f` | made by someone on the team. **Has no columns and no jobs** on 2026-10-12; still listed as a route board |
| 18 | Inspection TEST | `dcfe9eee-e5fa-4f39-96a3-910174ac3a46` | test board, no columns, no jobs |
| 19 | AHS - TEST | `d850b8e1-5026-4282-aa01-d55267c603f4` | the 2026-09-27 test board (9 columns), no jobs |

Retail and AHS - Repair & Review used to be listed lower; Zuper now shows them 1–3.

**Columns, live order, with Status Type** *(2026-10-12)*

- **AHS - Inspection** (17): Work Order Received `NEW` → Not Answering -Keep Call! `LEAD` →
  Welcome Call! `LEAD` → Scheduled `ESTIMATING` → Inspection: Day-Before Cal `ESTIMATING` →
  Inspection: Same-Day Confi `ESTIMATING` → Antonio On the Way `ON_MY_WAY` → Inspection In Progress
  `STARTED` → Inspection Completed `ESTIMATING` → Submit To AHS For Approval `ESTIMATING` →
  Awaiting AHS Decision `ESTIMATING` → **AHS Approved** `SOLD` → Proposal Made `ESTIMATING` · side:
  Waiting for Customer `ON_HOLD`, Reschedule Required `ON_HOLD`, Estimate Declined `LOST`,
  Cancelled `CANCELED`. (The two "Inspection: …" names are cut short in Zuper itself.)
  "Contact Attempted", "Inspection", "Notify Auth Dept", "Approval Requested", "Approved" and
  "Call Back" no longer exist on this board.
- **AHS - Repair & Review** (16): Not Answering- Keep Call! → Repair Scheduling Call → Day-Before
  Call (all `PRODUCTION`) → On the Way to Repair `ON_MY_WAY` → Repair In Process `STARTED` →
  Repair Complete `COMPLETED` → Call Satisfaction Check `PRODUCTION` → Invoice Submitted to AHS →
  Awaiting AHS Payment (`INVOICING`) → Paid `PAID` → Review Requested → Review Received (`OTHER`) ·
  side: Call Back `FOLLOW_UP`, Waiting for Customer, Reschedule Required (`ON_HOLD`), Cancelled
  `CANCELED`. "Repair Scheduled" no longer exists.
- **Retail** (and the 6 regional copies, identical, 16): New Lead `NEW`, Contact Attempted,
  Inspection / Estimate `STARTED`, Proposal Made, Estimate Sent, Follow Up `FOLLOW_UP`, Scheduled
  `SCHEDULED`, Repair In Process `STARTED`, Repair Complete `COMPLETED`, Invoice, Collect Balance,
  Paid `PAID`, Waiting for Customer, Reschedule Required (`ON_HOLD`), Estimate Declined, Cancelled
  `CANCELED`; the unmarked ones are `OTHER`.

A job keeps the status it had when it was moved to another board, so a few jobs show a column
name that board does not have (one AHS - Repair & Review job still reads "Scheduled").

**Job counts on 2026-10-12:** 461 jobs. AHS - Inspection 65 (incl. test jobs #701 and #734),
AHS - Repair & Review 305 (215 Paid, 34 Invoice Submitted to AHS), Retail 91 (38 Cancelled,
21 Paid). Every other board: 0.

### 3.2 Hand-off Inspection → Repair — SWITCHED OFF (2026-09-30)

The `zuper_tasks` container used to move an AHS - Inspection job at "Approved" to AHS - Repair &
Review. **Removed 2026-09-30 at the owner's request** — nothing we run may move a job between
columns or boards (backup `app/tasks_sync.py.bak-20260930` on owen-main). It had already stopped
matching: the team rebuilt the board and the column is now "AHS Approved", followed by "Proposal
Made". Moving a job to the Repair board is done by a person in Zuper.
By hand: edit the job's Category in Zuper and pick the stage.

### 3.2a Status Types drive the job page's Progress Tracker (2026-09-30)

The tracker on a job's page (Lead → Estimating → Sold → Production → Account Receivable →
Completed, plus Lost) follows each column's **Status Type**, not the column. Zuper offers the
types LEAD / ESTIMATING / SOLD / LOST / PRODUCTION / INVOICING only to roofing and HVAC companies
(ours is roofing). With the owner's OK, 18 AHS columns were set on 2026-09-30 by
`/home/qa/zbrowser/set_types.py` (dry run by default; changes only `status_type`, compares every
other field of every column after each write). Backup:
`/root/backups/ahs_statuses_before_types_20260930.json`.

- AHS - Inspection: Not Answering, Welcome Call! → LEAD · Day-Before Call,
  Same-Day Confirm, Scheduled, Inspection Completed, Submit To AHS, Awaiting AHS Decision,
  Proposal Made → ESTIMATING · AHS Approved → SOLD · Estimate Declined → LOST.
  **Work Order Received stays `NEW`** (it was set to LEAD on 2026-09-30 and put back on
  2026-10-01, verified 2026-10-12): a job created through the API with no status (the AHS email
  path) lands in the board's `NEW` column. With no `NEW` column it gets no status and shows in
  no column (that stranded #720–#724 for a day). Every board that receives API jobs needs one.
- AHS - Repair & Review: Not Answering, Repair Scheduling Call, Day-Before Call, Call Satisfaction
  Check → PRODUCTION · Invoice Submitted to AHS, Awaiting AHS Payment → INVOICING.
- **Left alone on purpose:** Paid / Cancelled (PAID / CANCELED — `tasks_sync.CLOSED_TYPES` and the
  two saved board views read them), On the Way / In Progress / Repair Complete (field-app
  behaviour), side columns (ON_HOLD / FOLLOW_UP). **Never use CLOSED** — Zuper then refuses any
  further status change on the job. Retail and the regional boards are not done.
- **A job keeps a copy of the type it entered its column with** (`current_job_status.status_type`);
  changing a column does not change the jobs already in it. They pick up the new type the next
  time they move.
- The dashboard's Job Statistics (Yet to Start / In Progress / …) counts by type, so it regrouped.
  The CRM mirror maps by status uid and is unaffected.

### 3.3 Stage checklists (the forms that pop up on a stage move) *(2026-10-12)*

| Board / stage | Questions | Notes |
|---|---|---|
| AHS - Inspection / **Welcome Call!** | 18 (intake) | the intake moved here when the team rebuilt the board ("Contact Attempted" is gone); 17 copy to job fields ("Checklist" group); one is required |
| AHS - Inspection / **Inspection Completed** | 18 (13 written + 3 photo sets + video + voice note) | moved here from "Inspection" 2026-09-25 (owner: pop up after the inspection). 2026-10-12: + the inspection questions (section 3.3a); Basic / Expanded / Comprehensive option made required. 13 written ones copy to job fields |
| AHS - Inspection / AHS Approved | 1 (KPI) | `AHS authorized ($)`, required |
| AHS - Repair & Review / Repair In Process | 1 (Photos - before) | |
| AHS - Repair & Review / Repair Complete | 3 (Photos - during / after, `Video - finished repair`) | the video is required (2026-10-12) |
| AHS - Repair & Review / Invoice Submitted to AHS | 3 (KPI), all required | `Customer chose`, `Customer paid ($)`, and `Line items added (AHS amount + any customer upgrade)?` (the third is not in `ZUPER-KPI-SETUP.md`) |
| Retail / Contact Attempted | 15 (intake, no AHS-only questions) | copy to job fields; one required |
| Retail / Inspection / Estimate | 12 | 9 written copy to job fields; none required (it pops up when the job ENTERS the column, before the visit) |
| Retail / **Proposal Made** | 6 (2026-10-12) | the inspection questions (section 3.3a) — the first move after the visit |
| Retail / Scheduled | 2 (KPI), required | `Option chosen`, `Sold price ($)` |
| Retail / Repair In Process, Repair Complete | 1, 2 photo questions | |
| Cancelled, on AHS - Inspection, AHS - Repair & Review and Retail | 2 (KPI) | `Cancel reason` (required), `Cancel note` |
| 6 regional boards | Retail's intake, inspection and photo checklists, **not** the KPI ones | **inspection questions NOT linked to job fields** (0 of 12, checked 2026-10-12) |

Field types Zuper's builder offers (new UI `field-form-builder` component): short / long text,
date, time, date-time, single choice (`RADIO`, component `radio`), multi choice (`MULTI_ITEM`,
`checkbox`), dropdown (`SINGLE_ITEM`, `select`), upload (`FILE`, `file`), `IMAGE`, `MULTI_IMAGE`
(`multi_image`), **`VIDEO` (`video`)**, `MULTI_MEDIA` ("walkthrough"), barcode, lookup, table,
signature, header. **There is no audio type** — a voice memo goes in a `FILE` question.

### 3.3a The inspection questions (2026-10-12, owner OK)

What the office needs from every inspection, asked when the technician finishes it:

| Question | Type | Required | Job field (group Checklist) |
|---|---|---|---|
| Roof type | single choice: Shingle · Tile · Metal · Flat · Other | yes | `Roof type` |
| Roof age (years) | short text | yes | `Roof age (years)` |
| Leak location | long text | yes | `Leak location` |
| Possible add-ons | multi choice: None · Gutters · Flashing · Skylight · Ventilation · Other | yes | `Possible add-ons` (text) |
| Video - roof condition | video | yes | — |
| Voice note (optional) | file upload | no | — |

On **AHS - Inspection / Inspection Completed** (added after the 12 that were there; the existing
Basic / Expanded / Comprehensive option questions — the Good / Better / Best suggestions — became
required) and on **Retail / Proposal Made**. AHS - Repair & Review got only `Video - finished
repair` (required) at Repair Complete: the inspection answers are already on the job, and the
Checklist field group shows on all three boards.

- **The technician can see and fill them before moving the job:** the answers are linked job
  fields with pre-fill on, so typing them on the job page during the visit pre-fills the pop-up.
  On AHS jobs the "2. Inspection" task lists every question too.
- **Not verified on a phone yet:** that the technician's app records the video straight from the
  camera, and how the task shows a video (the task job counts any link as "photos"). Test on #701.
- The optional voice note is a to-do in the "2. Inspection" task, so a finished inspection without
  one reads 17 of 18 (In progress) there.
- How it was done: `/home/qa/zbrowser/insp_checklist_v2.py` on dispatch (dry run by default; the
  `test` step tried every question type on the empty AHS - TEST board and removed them). Backup:
  `/root/backups/zuper_checklists_fields_before_insp_v2_20261012_201200.json`. `zuper_tasks` was
  paused during the change; a simulated full run showed no task status change, only the new
  to-do lines.
- **Proof nothing was lost:** `job_snapshot.py` (every job: fields, checklist answers, status) and
  `photo_snapshot.py` (every attachment id in the account) before and after:
  462 jobs, 2,913 filled fields, 771 checklist answers and 1,767 photos / files, identical.
  Snapshots in `/root/backups/zuper_jobs_*_inspection_checklist_20261012*` and
  `zuper_photos_*_inspection_checklist_20261012.json`. Use the same two scripts around any
  future settings change (`--compare <before> <after>`).

Deleting a checklist question keeps the answers already saved on jobs (tested). Answers live in
each job's status history (`job_status[].checklist`), and linked ones also in job fields.

### 3.4 Job fields (custom fields)

- 79 JOB fields on 2026-10-12 (40 on 2026-09-25; +4 inspection fields that day). 16 groups:
  **Checklist** (17 intake + 9 inspection + Roof type, Roof age (years), Leak location,
  Possible add-ons, shown on AHS - Inspection, AHS - Repair & Review and Retail), **AHS** (Authorization
  Amount, Customer Responsibility, Estimate Amount…), **CRM** (Callback Status…), **KPI** (one per
  KPI question, 2026-09-30), seven **AHS TEST - …** groups for the test board, and Zuper's empty
  Insurance, Warranty, Trades, Source Attribution, Drip Campaign State. Workiz Job #, Job Type,
  Technicians, Technician are ungrouped on every job — that is how the original load created them.
- **Technician** (dropdown) drives routes and assignment; **Technicians** (text) is Workiz's value.

### 3.5 Tasks on jobs (automatic)

Every live AHS job (both AHS boards; not Paid / Cancelled) has 4 tasks, maintained by the
`zuper_tasks` container:
`1. Intake call`, `2. Inspection`, `3. Repair - before photos`, `4. Repair - during & after photos`.
Each description is that stage's checklist as a to-do list with the answers
(`☑ Question: answer`, photos as "N photos"). A to-do is filled from the stage checklist OR the
linked job field. Status follows: Open → In progress → Completed; **a status a person changed
by hand is never overridden**. Never deletes a task. Retail / regional boards: not switched on
yet (owner: "AHS first").

### 3.6 Proposals (Good / Better / Best)

- Layout **"DTR Proposal"** (`e9b8add9-8fb8-44a4-9406-d8b9b7bec422`, default): contract pages
  (14 PDF images) + Your Options + Acceptance & Signature. Contact on every page:
  (954) 420-7373 / owen@dreamteamroofingfl.com. The contract **image** page 1 still shows the old
  (561) number and gmail (baked into the image), page 14 has an "INITIALS" line.
- Proposal templates: `DTR - Flat Roof Leak Repair`, `DTR - Tile Leak Repair`,
  `DTR - Roof Replacement`, `DTR - Custom` (each GOOD / BETTER / BEST). A template's options point
  at **"DTR Packages" products** (8); a new proposal copies the product description. Descriptions
  are HTML (`<p>`, `<ul><li>`) so Zuper's editor shows bullets — keep them that way.
- **`zuper_proposals` container** (section 4): for every DRAFT proposal it builds
  `Proposal-DTR-N.pdf` = Zuper's own PDF with the three stacked option pages replaced by one
  side-by-side page, signature page removed (customers sign online only), images downsampled
  (~6 MB). Rebuilt ~1–2 min after a draft changes; a sent proposal is never changed. Only that one
  file is attached, because Zuper's Send dialog pre-ticks every attachment.
- Company settings: `estimate.attach_pdf_in_mail = false` (only our PDF goes out),
  `estimate.mandate_deposit_for_accept = false` (sign without paying), `estimate.public_link = true`
  (the "Click to view Proposal" link, where customers sign).

### 3.7 Invoices

- Templates: **"Invoice"** (`57862374…`, had Zuper sample data hard-coded → replaced with DTR
  name, Bradenton address, (954) 420-7373, owen@…, License CCC1334317) and **"Default Invoice
  Template"** (`8e3fa26b…`, reads the company profile). `estimate.invoice_template` points at the
  default one. Payment terms text is still Zuper's sample (15 days, 15% late fee) — owner to decide.
- Company profile (`GET /user/company`, `PUT /company`): phone +19544207373, the DTR logo,
  timezone **America/New_York** (was Los Angeles until 2026-09-25).

### 3.8 Company config that matters

`jobs.enforce_sequential_execution = false` (techs may close route jobs in any order) · all
customer notification rules inactive, and every workflow inactive except the six in 4.5 ·
System Notification Number 19544207373 with system notifications ON (2026-10-13) · `jobs.public_link = false` · invoice public /
payment links off · `jobs.notify_user_on_assignment` on (staff only; `assign_sync.py` switches it
off while it runs) · `messaging.notify_users` = 2 users (Owen, Luis) · company timezone
America/New_York · vendor auto-emails on purchase/service orders are on (vendors, not customers).
All of these were re-read on 2026-10-12 and match.

**Webhooks in Zuper (2026-10-12): 11, none of them the CRM's.** All are Zuper's template from
2026-09-10. Nine point at Zuper's own workflow host. Two active ones, "test" (`job.schedule`) and
"new measurement" (`measurement.new`), post to **webhook.site**, a public third-party service.
They were reported to the owner and left alone. List them with
`GET https://us-east-1.zuperpro.com/service/notifications/webhook` (no `/api` in the path).

### 3.9 Routes and technicians

The routes job (section 4.3) builds one route per technician per day ("<Tech> - <Day>"), stops
in appointment-time order. `assign_sync.py` (manual, dry run by default, `--apply`) makes each
live job's assigned user match its Technician dropdown. Technicians: Owen, Antonio (own login
since 09-22).

---

## 4. What runs on its own

Verified on the servers 2026-09-30. Four jobs on two servers, plus the six customer-text
workflows that run inside Zuper itself (4.5). **The routes job is on dispatch,
not owen-main** — it was never moved (checked: no cron, timer, container or file for it on
owen-main).

| # | Job | Server | Every | Writes to Zuper? |
|---|---|---|---|---|
| 4.1 | `zuper_proposals` container — proposal PDFs | owen-main | 60 s | yes (attachments on DRAFT proposals) |
| 4.2 | `zuper_tasks` container — checklist tasks (hand-off OFF since 2026-09-30) | owen-main | 120 s | yes (tasks only) |
| 4.3 | `zuper-routes` cron — technician routes | **dispatch** | 1 min (full run every 5) | yes (routes; toggles a staff setting) |
| 4.4 | CRM mirror (`ghl_clone_api` / `ghl_clone_worker`) | owen-main | 15-min sweep (no webhook has ever arrived) | only the AHS-email create, and signed-proposal line items |
| 4.5 | **Six Zuper workflows that TEXT AHS CUSTOMERS** (reminders, column texts, thank-yous) | inside Zuper | on schedule / column move / incoming text | no job writes; **sends customer texts** from (954) 420-7373 |

All four were running on 2026-10-12. The three safety-gate failures in the recent routes log and
the two in the proposals log were Zuper 502s (the gate could not read Zuper, so nothing was
written); the tasks log shows the same occasional 502s, retried on the next loop.

All three scripts that write run `zsafety.py` first and write nothing when it fails (the log
says `SAFETY GATE FAILED`). None of them sends a customer anything.

**4.5 is LIVE since 2026-10-13** — the only automatic customer messaging in the account. Everything
about it (wording, rules, uids, the zsafety allow-list `APPROVED_WORKFLOWS`, how to stop it, test
customers) is in **`ZUPER-CUSTOMER-TEXT-WORKFLOWS.md`**. Rule 1 above was lifted for exactly those six.

### 4.1 `zuper_proposals` (owen-main)

- Loop: `/home/qa/zbrowser/run.sh` inside the container → `compare_sync.py --apply`, sleep 60.
- Host folder `/opt/santiagoproperties/zuper-proposals/` (`app/`, `data/compare_sync.log`,
  `data/state.json`). Moved from dispatch cron `zuper-compare` on 2026-09-24 (old file in
  `/root/backups/cron.d-zuper-compare.disabled-20260924`).
- What it does: section 3.6. Only lines with "rebuild", a traceback or ERROR reach the log.

### 4.2 `zuper_tasks` (owen-main)

- Loop: `/app/run.sh` → `tasks_sync.py --apply` (25-min timeout), sleep 120.
- Host folder `/opt/santiagoproperties/zuper-tasks/` (`app/tasks_sync.py`,
  `data/tasks_sync.log`, `data/tasks_state.json`). Env `TASKS_EXTRA_JOBS` kept test job #694 (deleted, see section 7) in
  scope.
- What it does: the 4 tasks on every live AHS job (3.5). The hand-off (3.2) is switched off.
  It turns EVERY column that has a checklist into a task, except the KPI-question columns in
  `NO_TASK_STAGES` (AHS Approved, Invoice Submitted to AHS, Cancelled — 2026-09-30). **Add a
  column there before giving it a checklist that is not a to-do**, or ~100 AHS jobs get a new task. A run that changed something logs `APPLIED | {jobs, created, described, status,
  manual, skipped}`.

### 4.3 `zuper-routes` (dispatch) — rewritten 2026-10-01

- Cron `/etc/cron.d/zuper-routes`: `* * * * * root /home/qa/zbrowser/routes_sync.sh` (**every minute**
  since 2026-10-01; 5 min before that). The wrapper takes a lock (`/tmp/zuper-routes.lock`), runs the
  safety gate, then `routes_sync.py --apply --watch`.
- **`--watch`:** the script lists every job and compares it with the last run (`routes_watch.json`:
  status, schedule, board, assignees with their `assigned_at`, Technician dropdown, map point). Nothing
  changed → it stops ("no job changed - nothing to do"). Something changed, or 5 min since the last full
  run → full run.
- **A status change rebuilds the route (owner, 2026-10-01):** when a job on a route changes status,
  that day's route is sent again (`REBUILD`) even if the stops are the same.
- **Whose route: the person ASSIGNED LAST (owner, 2026-10-01).** Among the job's assignees whose name is
  on the Technician dropdown's list, the one with the latest `assigned_at` gets the job — when two
  people are on a job, the last one assigned is the one going. Only when no assignee is a technician
  does the dropdown decide. The job is never changed (dropdown and assignees stay); a job with two
  technicians, or a dropdown that disagrees, is logged once as `WARN ... the route goes to X`.
- **Today's routes are kept up to date all day.** Zuper DOES accept a change to a route that has
  departed, as long as the new departure is in the future (tested 2026-10-01 on a throwaway route with
  test job #701; otherwise `400 Departure in past`, also on create). So a change after departure
  re-sends the route leaving now + 3 min, shown as `depart ... (now)`. (Until 2026-10-01 the script
  froze today's route at departure and same-day jobs were silently left off — that day Antonio's
  route held 1 of his 4 jobs.)
- **Which jobs:** scheduled, not Paid / Cancelled (by name or status type PAID / CANCELED), **not in a
  "Reschedule Required" column** (2026-10-06: such a job keeps its old date, and was still routed), today up to
  60 days ahead, on Retail, AHS - Inspection, AHS - Repair & Review (**required** — the run refuses if
  one is missing) and Leak repair, Repair Service (optional). Every stop of the day stays on the route
  in appointment-time order, done or not.
- **Logged once** (WARN, remembered in `routes_watch.json`): a scheduled job with no technician; an
  open job in a visit column (Scheduled / Day-Before / Same-Day / On the Way / Repair In Process /
  Arrived — not Reschedule Required) with no appointment time; two technicians on a job. A scheduled
  job on any other board is logged once as `SKIPPED` (`routes_skipped.json`).
- The log `/home/qa/zbrowser/routes_sync.log` gets a run only when it printed CREATE / UPDATE /
  REBUILD / REMOVE / TAKEN OFF / JOB CHANGED / SKIPPED / WARN / an error; the last output is always in
  `/tmp/zuper-routes.out`.
- **The route:** "<Technician> - <Day>", assigned to the technician's login (`LOGINS`: Owen, Antonio;
  others unassigned, for the office). Antonio's starts at his home (North Lauderdale) with departure =
  first appointment minus a rough drive estimate; everyone else's at the first stop. Never calls
  Optimize, no end location.
- Cleans up: takes a job off a route when it no longer belongs there, deletes its own emptied routes
  (today's too) and the old `Repairs - <Day>` ones; never touches a route with any other name.
- While it writes it switches `jobs.notify_user_on_assignment` off and restores it, and checks
  afterwards that no job's time, assignee or fields changed (`jobs verified unchanged: N of N`).
- Dry run (reads only, saves no state): `python3 /home/qa/zbrowser/routes_sync.py` as root on dispatch.
  Backups: `routes_sync.py.bak-20260918/-20260922/-20260928/-20260930/-20261001/-20261006`,
  `routes_sync.sh.bak-20260930/-20261001`, `/root/backups/cron.d-zuper-routes.bak-20261001`.
- **Pause:** comment out the line in `/etc/cron.d/zuper-routes`. Never run two copies (e.g. a
  second one on owen-main) — they would fight over the same routes.

### 4.4 The CRM mirror (owen-main, repo `OwenUSA/ghl-clone`)

- `.env.prod` (2026-10-12): `ZUPER_SYNC_ENABLED=true`, `ZUPER_PULL_ONLY=true`,
  `ZUPER_AHS_EMAIL_CREATES_JOBS=true`, `ZUPER_PROPOSAL_LINES=true`, `DISPATCH_ENABLED=true`,
  `ZUPER_REMINDERS_ENABLED=false`, `ZUPER_API_KEY` set. Production runs `966c0b7`.
- Reads Zuper with a **15-minute sweep only**. The code can take a webhook, but none is
  registered in Zuper and `last_webhook_at` has never been set (the one registration attempt,
  2026-09-23, was interrupted, and `webhook.EVENTS` uses `JOB_NEW`-style names; the real ones are
  lowercase and dotted, e.g. `job.schedule`). A synced card's Zuper-owned fields answer "Change
  this in Zuper".
- **The CRM's boards are still the OLD ones (checked 2026-10-12).** It has two pipelines:
  "Dream Team Roofing AHS" (19 stages: Work Order Received, Contact Attempted, Scheduled, On My Way,
  Inspecting, … Approved, Repair Scheduled … Paid, side columns) mapped to AHS - Inspection, and
  Retail (matches Zuper). **There is no AHS - Repair & Review pipeline and no regional pipeline**,
  although the code lists them in `mapping.MIRROR_ONLY_CATEGORIES`: `mirror --phase boards` has
  never been committed on production. The AHS cards' stages therefore do not follow most of the
  current Zuper columns (Welcome Call!, Submit To AHS For Approval, AHS Approved, …).
  The Dispatch page and the KPI history read Zuper directly and are not affected.
- **The one write:** an AHS work-order email that owen-main relays creates the customer, the job
  (lands in AHS - Inspection / Work Order Received — no status is named) and the work order as a
  note. Three POSTs, nothing else (DECISIONS.md, 2026-09-28).
- The bell alerts admins and dispatchers about a new job (and the other Dispatch events) —
  `app/dispatch/alerts.py`, rung by the Dispatch pass.
- **A new stage in Zuper is unknown to the CRM** until `python -m app.zuper.mirror --phase boards`
  is run (dry run first); until then a job moved into it does not move in the CRM. See the point
  above: on 2026-10-12 that is most of the AHS columns.

**Changing a container's code:** edit `app/*.py` on owen-main, then
`docker compose stop` → `docker compose build` → **`docker compose up -d --force-recreate`**, and
check the new code is inside (`docker exec <name> grep … /app/...`). `docker start` restarts the
*old* container — that once ran stale code and created 94 duplicate tasks (cleaned up).
**Pause `zuper_tasks` (`docker compose stop`) before changing any checklist or stage it reads.**

---

## 5. Scripts on dispatch (`/home/qa/zbrowser/`, run as root with `python3`)

| Script | Does |
|---|---|
| `zsafety.py` (in `/home/qa/`) | the safety gate |
| `ck_api.py` | checklist helpers (list, create, status lookup) |
| `ck_copy_retail.py` | copy Retail's checklists to the 6 regional boards (idempotent) |
| `insp_fields_link.py`, `insp_fields_link_retail.py` | 9 inspection job fields + link the inspection checklist to them |
| `move_insp_checklist.py` | moved the AHS inspection checklist to Inspection Completed |
| `ahs_split.py board|move|tidy` | the 2026-09-25 AHS split (dry run by default) |
| `tasks_sync.py` | the task job (same code as the container; `--job <uid>` for one job) |
| `build_full_proposal.py`, `gen_compare.py`, `compare_sync.py` | proposal PDFs (same code as the container) |
| `desc_html.py` | plain text ↔ Zuper's HTML descriptions |
| `assign_sync.py`, `routes_sync.py` | technician assignment and routes |
| `test_proposal9/10/11.py` | the test proposals (templates for making another) |

---

## 6. Workiz → Zuper sync (until Workiz is retired)

Folder `/home/qa/zsup/zmig/`. Exports go to `/home/qa/refs/zuper/workiz_jobs_NN.csv` (0640,
**real customer data — never paste rows**).

1. Copy the export: `scp "export_export (N).csv" dispatch:/home/qa/refs/zuper/workiz_jobs_NN.csv`
2. `cp diff19.py diffNN.py` and point it at the new CSV / `state/diffNN.json`; run it (read only):
   matched / changes / new.
3. `cp apply19.py applyNN.py` (**apply19 is the template** — 14–17 are older and unsafe) and run it
   dry, then `--apply`. It changes only schedule / status / Technicians, moves a job across the two
   AHS boards when needed, **never moves a status backwards**, and **skips a schedule if the job
   changed in Zuper after the export file was made**. Every change is read back.
4. New jobs: make a one-row CSV and use `run19_one.py <Job #> plan|load` — a **filtered** loader
   run (the unfiltered `p3_run.py all` would also create Zuper customers for every CRM contact).
   The loader does not set the Technician dropdown: add it, then `assign_sync.py --apply`.
5. `zdata.py` maps Workiz statuses → board + stage (AHS repair stages → AHS - Repair & Review).

Gotcha: writing job fields with only `{label, value}` strips their group (they show under "Other
Details"). Always send `label, value, group_name, group_uid, type`.

---

## 7. Test data (safe to use; only the owner's own email/phone)

- Customer **Santiago Villahermosa (TEST)** `2dbd7187-a6e8-4452-8600-c3d029599d75`
  (santiagovillahermosa@gmail.com, mobile **+15618788090** since 2026-10-09, "TEST - 100 Test St,
  Bradenton"). The old number +15618999051 is **not** ours and nobody knows whose it is — never
  use it.
- Customer **Owen Test** (+15616905516, Owen's phone).
- Job **#701** `6abd527f-31a6-43e9-bb0a-6fe557dac596` ("TEST - AHS TEST pre-fill check"), now on
  **AHS - Inspection / Submit To AHS For Approval** (2026-10-12), no technician.
- Job **#734** `2f14b7ff-7f94-4cc1-8aa2-dd62b067a6e7` ("TEST - Owen reminder test"), customer Owen
  Test, on **AHS - Inspection / AHS Approved**, no technician.
- Both test jobs sit on a real board, so they are in the job counts and show as `WARN … no
  technician` in the routes log. **#694 no longer exists** (404 on 2026-10-01; deleted by someone in
  Zuper, with its test proposals DTR-11…14 and invoice #1); `TASKS_EXTRA_JOBS` in `zuper_tasks`
  still names a job that is gone (404) — harmless.

---

## 8. Verified API cheat-sheet (all tested live)

| Action | Request |
|---|---|
| Create category | `POST /jobs/category {"category":{"category_name"}}` |
| Rename category | `PUT /jobs/category {"category":{"category_uid","category_name"}}` |
| Reorder categories (web JS) | `PUT /jobs/category/reorder {"categories":[{"category_uid","display_order"}]}` |
| Statuses of a category | `GET /jobs/status/{cat}` |
| Create status | `POST /jobs/status_new/{cat} {"job_status":{status_name,status_type,status_color}}` |
| Edit status (web JS) | `PUT /jobs/status/{cat} {"job_status":<FULL record incl. status_uid>}` — **never** `POST /jobs/status/{cat}` (bulk import) |
| Delete status (web JS) | `DELETE /jobs/status/{cat}/{status}` — jobs keep it in their history |
| Reorder statuses (web JS) | `PUT /jobs/status/{cat}/rearrange {"job_status":[uids]}` |
| Move job to another board | `PUT /jobs {"job":{"job_uid","job_category":uid}}` then set the status |
| Set job status | `PUT /jobs/{uid}/status {"status_uid","remarks"}` (with answers: `{job_uid,status_uid,status_name,customer_signature:"",checklist:[{question,answer,type}]}`) |
| Reschedule | `PUT /jobs/{uid}/update {"job":[{scheduled_start_time,scheduled_end_time,"type":"SCHEDULE"}]}` (plain `PUT /jobs` ignores times) |
| Job fields | `PUT /jobs {"job":{"job_uid","custom_fields":[…]}}` — **REPLACES the job's whole field list** (tested on #701 2026-10-06: one field sent, 33 became 1). Send every field the job has (GET it, drop `_id`, change only yours), then compare the rest after. Keep `label, value, group_name, group_uid, type` on each |
| Create job / child job | `POST /jobs {"job":{…,"due_date",["parent_job":uid]}}` |
| Checklist item | list `GET /settings/checklist?category_uid=&job_status_uid=` · add `POST /settings/checklist/new` · edit `PUT /settings/checklist/{uid}` · delete `DELETE /settings/checklist/{uid}` |
| Job field | add ONE `POST /settings/custom_fields/new {"module_name":"JOB","custom_field":{…}}` — **never** `POST /settings/custom_fields` (full-list replace; wiped 68 fields once) |
| Field group ↔ boards (web JS) | `PUT /settings/custom_fields/group/{uid} {"custom_field_group":{associated_to:[uids],category,group_name,module_name,group_description}}` |
| Tasks (web JS) | create `POST /service_tasks {"execution_type":"PARALLEL","is_enabled":true,"service_tasks":[{module:"JOB",module_uid,service_task_title,service_task_description,task_type:"TASK"}]}` · edit `PUT /service_tasks/{uid} {"service_task":{…}}` · status `PUT /service_tasks/{uid}/status {service_task_status,remarks}` · delete `DELETE /service_tasks/{uid}` · list `/service_tasks?filter.module_uid=` (`/jobs/{uid}/service_tasks` is 404) |
| Proposal PDF without sending | `POST /estimate/{uid}/send?send_proposal_layout=true {"template_uid"}` (NO `email` key) → PDF bytes |
| Send proposal | same + `email`, `subject`, `email_body`, `attachments:[attachment_uid strings]` (objects are silently ignored), then `PUT /estimate/{uid}/status {"estimate_status":"AWAIT_RESPONSE"}` |
| Edit proposal options | `PUT /estimate/{uid} {"estimate":{estimate_uid,is_proposal:true,proposal_title,proposal_options:[…]}}` |
| Attach file to proposal | `POST /misc/upload` (multipart) then `POST /note_attachment {module_name:"ESTIMATE",…}` |
| Company config | `PUT /company/config {"config_uid":<company_config_uid>,"config":<full config minus ids/timestamps>}` |
| Company profile | `GET /user/company` · `PUT /company {"company":{…}}` |
| Product | `PUT /product/{uid} {"product":{…}}` |
| Invoice template | `PUT /invoice_estimate/template/{uid} {"template":{…}}` |

---

## 8a. Calls and messages — where to look before updating a job (2026-10-01)

Before you move or update a Zuper job, check what was last said to the customer. There are
**two phone systems**, and each one keeps its own half of the story:

| | Quo (OpenPhone, `+1 954-482-9099`) | Zuper Connect (calls made inside Zuper) |
|---|---|---|
| Transcript | **Yes**: the full text of each recorded call | **No**: Zuper never exposes one (only the recording) |
| Summary | no | **Yes**: a summary, a "next action", a confidence score and a sentiment |
| Recording | yes | yes (`recording_url`) |
| Texts | yes, in the CRM inbox | not read by the CRM (unchecked whether the API allows it) |
| Where it lands in the CRM | Conversations (`conversation_events` / `number_thread_events`) | `dispatch_calls`, read every ~150 s by the Dispatch reader |

### Easiest: ask the Dispatch assistant
In the CRM, open **Dispatch** and ask, for example, "what was the last call with Jane Doe" or "job
#712". Its `get_job` tool returns the job together with every call and text from **both** systems
for that customer's phone numbers over the last 21 days (`COMMS_DAYS`): Quo transcripts and
Zuper Connect summaries. Matching is by the last 10 digits of the phone, so a call to a number
that the Zuper customer record does not have is not shown.

### Quo calls and texts in the CRM
- Browser: **Conversations** → the customer's thread. Each call shows its transcript under it
  and plays the recording. A number no contact holds has its own thread (`kind: "number"`).
- `ghl` CLI: `ghl calls list --q <word>` searches **transcripts**; `ghl convos show "<name>"`
  prints a thread in time order. The CLI talks to `http://127.0.0.1:8000` unless `GHL_API_URL` /
  `GHL_API_TOKEN` point it at production. **ctrl+K search does NOT search transcripts.**
- How Quo gets in: owen-main mirrors Quo (webhook plus poll). See owen-main
  `docs/QUO_WEBHOOK.md` and the 2026-09-13 / 2026-09-14 entries in `DECISIONS.md`. If the status
  dot shows Quo sync red, recent Quo calls are missing.

### Zuper Connect calls
- **The summaries exist, but Zuper's call list omits them.** The list returns `call_summary` as
  `{status: null, sentiment}`, and the text is only in the call's **details**. That is why the
  Zuper web app shows a summary only after someone opens the call. Since `a098402` (2026-10-01),
  the Dispatch reader opens up to 15 recorded calls per pass (newest first, last 14 days) that
  have no summary yet. It stores `"<summary>\nNext step: <next action>"` in
  `dispatch_calls.summary` and never opens that call again. A call whose summary Zuper has not
  written yet is retried on the next pass.
- API (read-only, same `x-api-key`), base `https://us-east-1-connect.zuperpro.com/api/telephony`:
  - `POST /calls` with body `{"page":1,"limit":100,"sort":"DESC","sortBy":"created_at","filters":{"current_user":false}}`
    returns the history of every user's calls. `call_modules[]` links a call to a job
    (`module: "JOB"`, `module_uid`).
  - `GET /calls/{call_uid}/details` returns `call_recordings[].call_summary` =
    `{summary, next_action, confidence, sentiment, status}` plus `recording_url`.
    There is no transcript field.
  - In the CRM these are the ONLY two Connect requests `client.connect_read` will make. Dial,
    hang up, `/message/send` and everything else are refused before a connection exists.
    **Never call any other Connect endpoint by hand. It can ring or text a customer.**
- Read what the CRM stored, on production (read-only):
  ```bash
  ssh owen-main
  cd /opt/santiagoproperties/ghl-clone
  docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T api python - <<'PY'
  from app.db import SessionLocal
  from app.models import DispatchCall
  db = SessionLocal()
  for r in db.query(DispatchCall).filter(DispatchCall.number == "9415550100") \
          .order_by(DispatchCall.occurred_at.desc()).limit(10):
      print(r.occurred_at, r.direction, r.status, r.duration_seconds, r.staff_name,
            r.job_uids, "\n   ", r.summary)
  PY
  ```
  (`number` is the customer's last 10 digits. `job_uids` lists the Zuper jobs the call was
  linked to.)

### Rules that still apply
- Reading calls changes nothing. **Updating the job does**, so follow section 1: only ADD
  (a note, a field), never move a job between columns and never touch gallery photos. Ask the
  owner before ANY change to a job, and say exactly what you will change.
- Never text or call a customer from a script. Do not paste transcripts or customer data into
  tickets, chats or files outside the server.
- Transcribing Zuper Connect recordings ourselves (for example with OpenAI audio) is possible
  but **not built**. The owner chose Zuper's summaries instead (Dispatch Q30). Ask before
  adding it.

---

## 9. CRM side (repo `OwenUSA/ghl-clone`)

- The CRM mirrors Zuper one way (section 4.4). `feature/zuper-new-pipelines` (the 6 regional
  boards, "AHS - Repair & Review", `CATEGORIES` AHS → "AHS - Inspection", `mirror.ALIASES`
  "Inspecting" → "Inspection", `mirror.MERGES` "On My Way" → "Inspection") is **merged to `main`
  and deployed** — production ran `b332d53` on 2026-09-28 and runs `966c0b7` on 2026-10-12. The
  code is deployed, but **its `--phase boards` was never committed** (section 4.4), so the CRM
  still has the old AHS pipeline.
- After a board or stage change in Zuper: in the api container
  `python -m app.zuper.mirror --phase boards --phase backfill` **dry run**, show the owner, then
  `--commit` with **boards and backfill together** (so no card sits on a wrong stage).
- Run the Zuper tests on dispatch — the Node-driven tests fail on Windows paths.
- **The main checkout `ghl-clone/` is shared with another session** (voice agents) — do not switch
  its branch.

---

## 10. Open items

- [x] ~~Deploy the CRM branch (section 9)~~ — deployed (`b332d53` on 2026-09-28; see section 9 for today's).
- [ ] **Run `mirror --phase boards --phase backfill`** (dry run first, show the owner): the CRM
      still has the old "Dream Team Roofing AHS" pipeline and no AHS - Repair & Review or regional
      pipelines (section 4.4, checked 2026-10-12).
- [ ] Webhooks: none registered for the CRM; fix `webhook.EVENTS` to the real dotted names before
      trying again. Two template webhooks post to webhook.site (section 3.8) — owner's call.
- [ ] The owen-main containers' `zsafety.py` is older than dispatch's (different file on
      2026-10-12; see `ZUPER-AUTOMATIONS.md` section 4).
- [x] ~~Routes: whose job when two people are assigned; warn about a job with no technician~~ —
      the person assigned last gets it, and both cases are logged as WARN (2026-10-01).
- [ ] Routes: on 2026-10-12 the log warns about open jobs in a visit column with no appointment
      time (#335, #287, #728, #294, #693, #738, #736) — they can be on no route until someone sets
      a time. #701 / #734 (test jobs) have no technician. (The 2026-10-01 list — #717, #481,
      #292 — is no longer warned about.)
- [ ] Routes: the regional boards (Miami / Sarasota) are not route boards; add them to
      `EXTRA_BOARDS` when they get scheduled jobs (a SKIPPED line in the log will say so).
- [ ] Decide whether the three writing jobs (4.1–4.3) stay under the 2026-09-27 read-only rule.
- [ ] Checklist tasks for Retail + the 6 regional boards (owner said AHS first); link the regional
      boards' inspection questions to job fields first. Give the regional boards the KPI
      questions (Scheduled, Cancelled) before they get jobs, or their jobs are missing from the KPI
      report.
- [ ] Leak repair has no columns. If the team means to use it, it needs a `NEW` column at least.
- [ ] Invoice payment terms (still Zuper's sample text); proposal email wording.
- [ ] Contract image page 1 (old phone/email) and page 14 "INITIALS" line — needs a new contract PDF.
- [ ] Optionally add a job field for "Intake complete?" or make it optional (so intake can be
      finished without the popup).
- [ ] Optionally move Retail + regional pipelines up the list and the unused template pipelines down.

---

## 11. Backups (dispatch `/root/backups/`)

Key ones from 2026-09-24/25: `ahs_jobs_full_before_split_20260925.json` (all 333 AHS jobs, full),
`ahs_statuses_*`, `ahs_checklists_20260925.json`, `ahs_inspection_checklist_before_move_20260925.json`,
`job_custom_fields_before_insp_20260925.json`, `job_field_groups_20260925.json`,
`job_categories_before_reorder_20260925.json`, `zuper_company_config_before_*`,
`zuper_company_profile_before_*`, `dtr_layout_before_*`, `dtr_products_before_html_20260924.json`,
`duplicate_inspection_completed_tasks_20260925.json`, `export19_before_apply.json`,
`job694_*`. Scripts keep `*.bak-<date>` copies next to themselves.
