# How Dream Team Roofing uses Zuper (operations)

Sources: `docs/ZUPER-*.md` on `origin/main` (2026-10-26), the unmerged branch
`docs/zuper-2026-10-12-evening` (`ghl-wt-zuperdocs`, has `ZUPER-PROPOSALS-AND-UPSELLS.md`), the
project's Claude memory notes (live changes up to 2026-10-27), `backend/app/*`. Dates say when a
fact was last checked. **Live state wins:** read `live-account.md` first; anything here older than
it may be stale. No customer data here — jobs are named by job number (`work_order_number`).

---

## 1. The company and the people

- Dream Team Roofing FL — roof repair/replacement, Bradenton/South FL, roofing account type in
  Zuper (unlocks LEAD/ESTIMATING/SOLD/PRODUCTION/INVOICING status types). Timezone America/New_York.
  Business hours (CRM rules): Mon–Sat 8 AM–6 PM; texting window 8 AM–8 PM.
- **Owen Buzaglo** — owner; Zuper Admin; also a technician; the production API key is his user,
  so every API write reads "by Owen". User uid `74cd936c-9386-469b-addc-d05d3d1a06a0`.
- **Luis Candiales** — office manager / operations (Admin + custom role "Office Manager/Operations",
  commission permissions since 2026-10-27); texts customers by hand from Zuper daily; receives
  staff alerts. User uid `4369d420-89d1-40e4-96aa-9091d96fb94b`, team `caa81dc7-20d2-4fb0-a2f2-efb4db645990`.
- **Antonio Brown** — field technician (Field Executive, Repairs team, own login since 2026-09-22,
  uid `6cc1e9dc-4210-40b5-91da-10bd61206496`); commission rules apply to him; route starts at his home.
- Technicians on the `Technician` dropdown: Owen, Antonio (others possible; unassigned routes go to the office).
- "The boss" = Owen's boss, origin of the read-only rule (2026-09-27). "The user" in notes = Santiago
  (the developer, runs these sessions); approvals must come from Owen (owner) — see `safety-rules.md`.
- Phone lines: **(954) 420-7373** Zuper Connect primary + system-text sender; (786) 882-0190 Zuper
  outgoing only; Quo/OpenPhone +1 954-482-9099 (office calls with transcripts, not in Zuper);
  CRM line +1 954-775-8492; reminder line +1 786-920-0331 (CRM, off).

---

## 2. Boards (Zuper "job categories") — 19, three in use

| # | Board | uid | Use |
|---|---|---|---|
| 1 | **AHS - Inspection** | `f07e3c01-97b1-404d-8929-175845774a60` | AHS warranty claims from work order to AHS approval |
| 2 | **AHS - Repair & Review** | `2d0160ea-cf71-46f5-9911-e43d80c42d96` | approved AHS repairs → paid → review |
| 3 | **Retail** | `5939c976-002a-4d80-8bfd-2669a1f8843f` | non-warranty customers |
| 4–10 | Lead Qualification, Roof Inspection, Repair Service (`e35524d8-6080-473a-b522-ce492b3735b1`), Production - Roof / Metal Roof / Gutters / Siding | — | Zuper templates, **no jobs**. Repair Service still a route board |
| 11–16 | Miami Retail Repair `520c342f-b1d6-4082-b047-2fd4039202c4`, Miami Retail Roof Replacement `bce5119a-58cd-46d9-8fa9-cb420eb8c940`, Miami Gutters `a29e16c7-590b-47ac-9527-162b71cdc488`, Sarasota Repairs `6af83eaa-50f0-426b-93ad-ee46afcd0b43`, Sarasota Roof Replacement `601fd5ee-5040-4d7f-8a9a-459e62e1a0e0`, Sarasota Gutters `fda33634-1655-45fb-8503-d5d3111745d6` | | Retail's 16 columns, **no jobs**; have intake/inspection/photo checklists but NOT the KPI questions, inspection questions not linked to fields; not route boards |
| 17 | Leak repair | `449461c8-4f24-411c-8215-004c7c94464f` | made by staff; **no columns, no jobs** (a job created here lands in no column) |
| 18 | Inspection TEST | `dcfe9eee-e5fa-4f39-96a3-910174ac3a46` | test, empty |
| 19 | AHS - TEST | `d850b8e1-5026-4282-aa01-d55267c603f4` | test board (9 columns), used to rehearse settings changes |

Counts 2026-10-12: 461 jobs (AHS - Inspection 65 incl. test #701/#734; AHS - Repair & Review 305
— 215 Paid, 34 Invoice Submitted to AHS; Retail 91 — 38 Cancelled, 21 Paid). Backup 2026-10-27: 480 jobs.

---

## 3. Columns (statuses), their type and meaning

A job's progress tracker (Lead → Estimating → Sold → Production → Account Receivable → Completed, + Lost)
follows the column's **Status Type**. A job keeps the type it entered with. Never use type `CLOSED`.
Moving a job **into** a column pops that column's checklist for the person moving it.

### 3.1 AHS - Inspection (17 columns, order 2026-10-12)

| Column | Type | Meaning / what the office does |
|---|---|---|
| Work Order Received | NEW | AHS work order arrived (email → CRM → Zuper creates the job here). Must stay type NEW: API-created jobs land in the NEW column. First contact pending (SLA 120 business min) |
| Not Answering -Keep Call! | LEAD | customer not reached; keep calling |
| Welcome Call! | LEAD | welcome call; **intake checklist (18 q, 1 required)** pops here; book the inspection |
| Scheduled | ESTIMATING | inspection booked (moved after Welcome Call! 2026-10-12) |
| Inspection: Day-Before Cal | ESTIMATING | day-before confirmation call (name truncated in Zuper) |
| Inspection: Same-Day Confi | ESTIMATING | same-day confirmation |
| Antonio On the Way | ON_MY_WAY | tech driving (field app) |
| Inspection In Progress | STARTED | on site |
| Inspection Completed | ESTIMATING | **inspection checklist (21 q since 2026-10-21)** pops: 13 written (linked to job fields), 3 photo sets, roof video, voice note, Basic/Expanded/Comprehensive (Good/Better/Best suggestions, required), the inspection questions (§4.2) |
| Submit To AHS For Approval | ESTIMATING | office submits the report/estimate to AHS (track-time-in-status on) |
| Awaiting AHS Decision | ESTIMATING | waiting on AHS |
| **AHS Approved** | SOLD | AHS authorized the repair; KPI q `AHS authorized ($)` required. Then a PERSON moves the job to AHS - Repair & Review |
| Proposal Made | ESTIMATING | upgrade proposal made to the customer |
| side: Waiting for Customer, Reschedule Required | ON_HOLD | Reschedule Required = keeps old date but is excluded from routes |
| Estimate Declined | LOST | |
| Cancelled | CANCELED | KPI `Cancel reason` (required) + `Cancel note` |

Gone (old names may still show on jobs not moved since): Contact Attempted, Inspection/Inspecting,
Notify Auth Dept, Approval Requested, Approved, Call Back, On My Way.

### 3.2 AHS - Repair & Review (18 columns since 2026-10-27; verify in live-account.md)

Order after 2026-10-27 changes (memory): Not Answering- Keep Call! → Repair Scheduling Call → Day-Before
Call (PRODUCTION) → Waiting for Customer, Reschedule Required (ON_HOLD, moved here) → On the Way to
Repair (ON_MY_WAY) → Repair In Process (STARTED; photos-before) → Repair Complete (COMPLETED; photos
during/after + optional `Video - finished repair`) → Call Satisfaction Check (PRODUCTION) → Review
Requested → Review Received (OTHER; moved here 2026-10-27) → **Invoice Submitted to AHS** (INVOICING;
3 required KPI q: `Customer chose`, `Customer paid ($)`, `Line items added (AHS amount + any customer
upgrade)?` — "No" blocks the move) → Awaiting AHS Payment (INVOICING) → Paid (PAID) · side: Call Back
(FOLLOW_UP), **Callback in Progress, Callback Done** (FOLLOW_UP, new 2026-10-27), Cancelled (CANCELED).
`ZUPER-OPERATIONS.md` §3.1 (2026-10-12, 16 columns) is outdated on this board.

### 3.3 Retail (and the 6 regional copies), 16 columns

New Lead `NEW` → Contact Attempted (intake 15 q) → Inspection / Estimate `STARTED` (12 q, pops on
entry) → **Proposal Made** (inspection questions, 9 q) → Estimate Sent → Follow Up `FOLLOW_UP` →
**Scheduled** `SCHEDULED` (KPI `Option chosen`, `Sold price ($)` required) → Repair In Process
`STARTED` → Repair Complete `COMPLETED` → Invoice → Collect Balance → Paid `PAID` · side: Waiting for
Customer, Reschedule Required `ON_HOLD`, Estimate Declined, Cancelled `CANCELED` (KPI cancel q).
Unmarked = `OTHER`. Retail jobs have **no line items** (2026-10-13) — sales invisible to line-item reports.

### 3.4 Stage groups the CRM uses (`backend/app/dispatch/config.py`)
- CLOSED: Paid, Cancelled, Estimate Declined, Review Received.
- First contact: Work Order Received, Welcome Call!, Not Answering -Keep Call!, New Lead.
- Needs a booking: those + AHS Approved, Repair Scheduling Call, Reschedule Required, Call Back.
- Needs a date: Scheduled, Day-Before…, Same-Day…, On the Way…, In Progress, Repair In Process.
- Waiting on AHS: Submit To AHS For Approval, Awaiting AHS Decision, Invoice Submitted to AHS, Awaiting AHS Payment.
- Done signals: Inspection Completed; Repair Complete / Call Satisfaction Check.
- SLAs (business minutes): new AHS 120, new Retail 60, after inspection 120, missed call 30, book 600, talked-not-updated 120; stale after 7 days.

---

## 4. Forms, fields, tasks, albums

### 4.1 Stage checklists summary
Intake at AHS Welcome Call! (18) and Retail Contact Attempted (15) — 17 copy to job fields; inspection
at AHS Inspection Completed (21) and Retail Proposal Made (9) / Inspection / Estimate (12); photos at
Repair In Process / Repair Complete; KPI questions at AHS Approved, Invoice Submitted to AHS, Retail
Scheduled, Cancelled (3 boards). Answers live in `job_status[].checklist` and, when linked, in job fields
(the field wins in reports; correcting a wrong answer = edit the field).

### 4.2 Inspection questions (2026-10-12; +3 optional 2026-10-21)
`Roof type` (Shingle/Tile/Metal/Flat/Other, req) · `Roof age (years)` (req) · `Leak location` (req) ·
`Possible add-ons` (None/Gutters/Flashing/Skylight/Ventilation/Other, req) · `Video - roof condition`
(req, not enforced by API) · `Voice note (optional)` (file) · `Tools & materials needed` · `Estimated
time to complete` · `Couldn't inspect yet (e.g. plywood under tiles)` (optional). Linked job fields in
group **Checklist** (same labels).

### 4.3 Job custom fields (84 on 2026-10-21)
Groups: **Checklist** (intake + inspection), **AHS** (Authorization Amount, Customer Responsibility,
Estimate Amount…), **CRM** (Callback Status, `Original job #`…), **KPI** (`AHS authorized ($)`,
`Customer chose`, `Customer paid ($)`, `Option chosen`, `Sold price ($)`, `Cancel reason`, `Cancel
note`), seven "AHS TEST - …" groups, empty Zuper template groups. Ungrouped on every job: Workiz Job #,
Job Type, Technicians, **Technician** (dropdown — drives routes/assignment). All fields are text
(no number type). Reports find answers BY LABEL — exact spelling matters.

### 4.4 Tasks on AHS jobs (automatic, `zuper_tasks` container)
Every live AHS job (both AHS boards, not Paid/Cancelled) has `1. Intake call`, `2. Inspection`,
`3. Repair - before photos`, `4. Repair - during & after photos`; description = that stage's checklist
as a to-do list (`☑ Question: answer`); status Open → In progress → Completed; a status a person
changed is never overridden; tasks never deleted. KPI columns are in `NO_TASK_STAGES`. Retail/regional: none.

### 4.5 Albums
Default job albums (copied onto a job only at creation, 2026-10-22): `360 Attic`, `360 Ground`,
`360 Roof`, `Leaks`, `For the Owner`. Field photos also arrive as IMAGE/VIDEO notes.

---

## 5. The AHS (American Home Shield) flow end to end

1. **Work order email** from AHS → owen-main parses it → `POST /api/ahs-jobs` → CRM card (AHS
   pipeline, idempotent on `ahs_job_id`) → CRM worker creates in Zuper (only automated job creation
   allowed): customer (`POST /customers_new`), job (`POST /jobs`, AHS - Inspection, `due_date` = today,
   no status, no visit) and the work order as a private note. Job lands in **Work Order Received**.
   Zuper has no email ingest; AHS addresses often have no city (accepted). One failure seen:
   "Customer Already Exists" (needs a person).
2. Office calls (SLA 120 business min) → Not Answering / **Welcome Call!** (intake) → books →
   **Scheduled** → day-before / same-day confirmation calls → tech On the Way → In Progress.
3. Tech finishes → **Inspection Completed** (inspection questions, photos, video, Good/Better/Best suggestions).
4. Office submits to AHS → **Submit To AHS For Approval** → **Awaiting AHS Decision**.
5. AHS authorizes. Evidence: AHS note email `Note Added in Frontdoor System NCC $#### Net Total $#### AUTHO # ####…`
   (owen-main can post it to the CRM as an "AHS approved" Dispatch item — off by default). Move to
   **AHS Approved** (answer `AHS authorized ($)`). Wording rule: "authorized", never "approved", to
   customers; never show amounts.
6. Optional **upgrade proposal** to the customer (Good/Better/Best above what AHS covers) → Proposal Made.
7. A **person** moves the job to **AHS - Repair & Review** (edit Category, pick stage; the old automatic
   hand-off is OFF since 2026-09-30 and must stay off). → Repair Scheduling Call → Day-Before Call →
   On the Way → Repair In Process → Repair Complete → Call Satisfaction Check → (Review Requested/Received).
8. Office adds AHS line items (`AHS-LEAK-REPAIR` $1,100, `AHS-TRIP` $100, `AHS-ADDL-LEAK` typed price)
   + any customer upgrade by **Invoice Submitted to AHS** (required question blocks otherwise) →
   Awaiting AHS Payment → **Paid**.
9. Callbacks (repair failed): a NEW job, `job_type` REVISIT + `parent_job` = original (Zuper refuses
   REVISIT without a parent); custom Job Type "Callback/Warranty" legacy. Never reopen/move the original.

---

## 6. What runs by itself against Zuper (state per `ZUPER-AUTOMATIONS.md` 2026-10-13 + memory 2026-10-26)

| # | What | Where | Writes to Zuper | State |
|---|---|---|---|---|
| A | Technician routes `routes_sync.py` | dispatch cron, every min (full run every 5) | routes "<Tech> - <Day>"; toggles `jobs.notify_user_on_assignment` | running; owns routes — never edit them |
| B | Checklist tasks `tasks_sync.py` | owen-main container `zuper_tasks`, 2 min | 4 tasks per AHS job | running |
| C | Proposal PDFs `compare_sync.py` | owen-main container `zuper_proposals`, 60 s | attaches `Proposal-DTR-N.pdf` (side-by-side options, no signature page) to every DRAFT proposal | running |
| D | CRM mirror sweep | CRM worker, 15 min | none (pull-only); CRM still has the OLD AHS pipeline | running |
| E | AHS email → Zuper job | CRM worker | customer + job + note | on |
| F | Signed proposal → job line items `proposals.py` | CRM worker after sweep | one `PUT /jobs` (products + job_total) | on (`ZUPER_PROPOSAL_LINES=true`) |
| G | Dispatch reader | CRM worker, 150 s | none (phase-3 writes built, OFF) | on |
| H | KPI history + nightly Excel | CRM worker 02:00 ET | none | on |
| I | Daily digest | CRM worker | refused by pull-only | dead |
| J | CRM reminder texts | CRM | none (texts via owen-main) | OFF since 2026-10-12 |
| K | **Zuper workflows texting AHS customers** (7) | inside Zuper | texts customers | **All 7 DEACTIVATED 2026-10-26** (user stopped all automated messages; memory). Repo docs still say LIVE — stale |
| L | CRM auto-reply / dropped-call / follow-up / reviews / payments texts | CRM worker | `POST /customers/{uid}/send` + private note | OFF (`ZUPER_AUTOREPLY_ENABLED=false`, `DROPPED_CALL_TEXTS_ENABLED=false` on 2026-10-26) |

Writers A–C run `zsafety.py` first and write nothing if any workflow not in `APPROVED_WORKFLOWS`,
any customer notification rule, customer-facing job alert/reminder, or messaging company switch is
active, or if Zuper errors. Zuper 502s are routine. Code for A–C is on the servers only (no git).

### 6.1 The customer-text workflows (inactive since 2026-10-26; record of what they do)
| Workflow | uid | Trigger | Text |
|---|---|---|---|
| DTR - AHS reminder - 1 day before | `d4f30597-d393-4d49-a5a8-e21b7bb2635b` | job scheduled/rescheduled (both AHS boards) | reminder, "reply YES"; 1 h later bell alert if unconfirmed |
| DTR - AHS reminder - 2 hours before | `79b9a4e4-05cc-464d-98f5-7c151aa51a90` | same | same |
| DTR - AHS text - Inspection Completed | `cffbe8da-1940-4bc4-9541-18b85e26153d` | move into column | rate technician 1–5 |
| DTR - AHS text - Awaiting AHS Decision | `5a41a844-cc97-494e-b676-3be55d646efd` | move into column | report submitted to AHS |
| DTR - AHS text - AHS Approved | `ad2e937e-1e86-4f93-9672-142cc777b5f2` | move into column | repair authorized, we'll call in 1 business day |
| DTR - Thank you for confirming | `79afb5bb-9272-427c-8a12-c3487b55f6a2` | webhook `sms.new_sms` (`5c798249-a7c9-4de9-98de-c2e8e6ef8e7d`) | thank-you to YES / rating |
| DTR - AHS welcome - new job | `a5de1df0-3223-491b-867d-f7fe901b59d1` | job created on AHS - Inspection | welcome, contact within 24 h |
Rules: 8:01 AM–7:50 PM NY, column texts once per job, skip Cancelled/Declined/Paid, sender (954)
420-7373, English only. Bell alerts fail on Zuper's side. 41 other workflows + 21 notification rules
off since 2026-09-17 (an import triggered 231 mistaken customer messages that day).

**Consequence for Owen AI:** while K is off, column moves and reschedules send nothing — but if
anyone re-activates them, *any* API column move or reschedule texts a real customer. Check
`live-account.md` / workflow state before any move or reschedule.

---

## 7. Money: proposals, line items, invoices, commissions, reports

### 7.1 Rules for proposals (owner/team rules, `ZUPER-PROPOSALS-AND-UPSELLS.md` 2026-10-13)
1. Create the proposal **from inside the job** (else never copied, counts on no board).
2. Use only DTR templates / DTR catalog items (`DTR-…-GOOD|BETTER|BEST`); free-typed items aren't recognised as upsells.
3. The customer must accept in Zuper (online "Review & Sign" link, `estimate.public_link=true`; no deposit required).
4. Settle the price before acceptance; invoice edits afterwards are not carried to the job.
5. AHS jobs: add AHS lines by hand by Invoice Submitted to AHS.
6. Retail: use a proposal even for a one-option sale.
7. Don't type an upgrade line on a job that will also get a signed proposal (copy stops `job_has_other_upgrade`).

Proposal templates (each GOOD/BETTER/BEST): `DTR - Flat Roof Leak Repair` (DTR-FLAT-*),
`DTR - Tile Leak Repair` (CUSTOM-GOOD, CUSTOM-BETTER, TILE-BEST), `DTR - Roof Replacement`
(CUSTOM-GOOD, SHINGLE-RR-BETTER, CUSTOM-BEST), `DTR - Custom` (CUSTOM-*). No shingle-repair template.
Layout "DTR Proposal" `e9b8add9-8fb8-44a4-9406-d8b9b7bec422` (default): 14 contract image pages + options
+ signature; page 1 image still shows an old phone/email; page 14 has an INITIALS line. Customers sign
online only. Company: `estimate.attach_pdf_in_mail=false`, `mandate_deposit_for_accept=false`.
Customer-facing proposal email wording is still an open item.

### 7.2 Line-item codes (`backend/app/kpi_report.py:380–456`, catalog 2026-10-13)
| Code | Catalog item | Catalog price | Report reads it as | Default commission (Antonio Brown, from 2026-01-01) |
|---|---|---|---|---|
| `AHS-LEAK-REPAIR` | AHS - Leak Repair | $1,100 | AHS paid | $350 flat |
| `AHS-TRIP` | AHS - Trip / Diagnostic | $100 | AHS paid | $50 flat |
| `AHS-ADDL-LEAK` | AHS - Additional Leak Repair | $0 (typed) | AHS paid | $350 flat |
| `UPG-GOOD/-BETTER/-BEST` | Customer Upgrade - Good/Better/Best | $0 | customer paid, that tier | 50% (`UPG-*`) |
| `UPG-UNKNOWN` | Customer Upgrade (tier not recorded) | $0 | customer paid, "not recorded" | 50% |
| `DTR-FLAT-GOOD/-BETTER/-BEST` | Flat Roof Leak Repair packages | $1,500 / $1,900 / $3,000 | customer paid, tier | 50% (`DTR-*`) |
| `DTR-TILE-BEST` | Tile Leak Repair BEST | $2,000 | Best | 50% |
| `DTR-SHINGLE-RR-BETTER` | Roof Replacement, Architectural Shingles | $5,200 | Better | 50% |
| `DTR-CUSTOM-GOOD/-BETTER/-BEST` | Custom Package (scope/price on site) | $0 | tier | 50% |
- Any `DTR-*` = upgrade; tier from the code's last word, else the item name's first word (`upgrade_tier`). $0 upgrade lines are not counted. The **line's own total** is used, not catalog price.
- Commission rules: `commission-rules.csv` (`technician,item,kind,value,from`; `kind` flat per unit / percent of line; `item` may end in `*`; latest `from` ≤ job date wins) in the KPI input folder on owen-main — **not in the repo**; built-in `DEFAULT_RULES` = table above. Zuper commissions themselves are created by hand/one-off loads (`POST /commissions`).
- Catalog: AHS category (~126 items incl. repair-type sets `AHS-TILE-NN`, `AHS-COMN-NN`…, price 0) and DTR Packages. Reports go by code, not category. Proposed not done: DTR-TILE-GOOD/BETTER, DTR-SHINGLE-RR-GOOD/BEST, a shingle-repair template.
- State 2026-10-13: 140 jobs have lines (all AHS); 32 `UPG-UNKNOWN` jobs stay as they are (never relabel a tier by amount).

### 7.3 Signed proposal → job (automatic, F)
Zuper's Convert to Invoice puts the option on the INVOICE only. The CRM, after each sweep, copies the
accepted option's lines onto the proposal's job (existing lines kept; job_total = sum only if it
equalled the lines before; once per proposal; skipped `no_job`, `zero_option`, `already_on_job`,
`job_has_other_upgrade`). Reports read job lines first, else live invoices linked to the job.

### 7.4 Invoices
Templates "Invoice" (`57862374…`, DTR branding hard-coded, License CCC1334317) and "Default Invoice
Template" (`8e3fa26b…`, the configured default). Payment terms still Zuper's sample (15 days / 15% late
fee) — open. Invoice public/payment links off. Zuper Pay collection exists; follow-up reminders exist
(check before any send). Known data issues 2026-10-13 (each fix needs the owner's OK): a proposal
accepted with a free item; a job line "Roof Repair" code `001`; job #274 proposal $375 vs invoice $1,500;
job #625 counted via invoice only; proposal #30 / invoices #2, #9 not linked to a job; proposal #49
(job #691) not converted.

### 7.5 KPIs and reports (CRM, read-only)
- Nightly `KPI-YYYY-MM-DD.xlsx` on owen-main (job numbers only): Summary (with CHECK lines), AHS
  upsells per job, upsells by month/tech, proposals by tier, income per job, commissions (+ by week,
  Antonio pay sheets), cancels with reasons, callbacks with original job + technician, time in column,
  moves, Workiz history, two Speed-to-lead sheets.
- Sources: `zuper_status_history` (append-only, every move) + `zuper_record_versions`; KPI questions
  (field first, else checklist answer); job line items.
- **Speed to lead** = CRM Reporting → Speed to lead (business minutes from job creation or the customer's
  own call/text to the first PERSON's call/text; targets AHS 120, Retail 60, missed call 30). Zuper itself
  only stores `time_on_status`; it can show a queue (saved view "Not contacted yet") or a breach count (a
  2-hour delay alert Work Order Received → Welcome Call!) — both proposed, not created (writes).
- Zuper's own Job Value / profitability: cost prices are $0, labor/expenses not set up → revenue only.
- Insights (CRM): AI tags every call/text (still leaking, complaint, upsell interest…).

---

## 8. Customer communication

- **People text by hand** from Zuper (Luis daily) and from Quo/CRM. That is the normal path.
- Before changing a job, check the last conversation: Zuper Connect (texts + call summaries — no
  transcripts) AND Quo (transcripts, in the CRM). CRM Dispatch `get_job` merges both for 21 days by
  last-10-digit phone match.
- Zuper Connect inbox has its own "CSR Agent" AI responder — inactive; the owner chose the CRM's
  approach. `needs_attention` flags belong to that responder.
- STOP/UNSUBSCRIBE handled by the carrier; a number blocked in Zuper is never texted.
- Automated texting history: 2026-09-17 incident (231 mistaken messages) → everything off; selective
  lifts (workflows K live 10-13 → 10-26; CRM auto-reply built 10-19, off) → **all automated customer
  messaging stopped 2026-10-26** on the user's word; re-activate only on the owner's word.
- Wording rules learned: "authorized" not "approved" for AHS; never show money in texts; no links
  except the official review link; English (Spanish exists in CRM templates only); include
  "Reply STOP to opt out" in automated texts; 8 AM–8 PM window.

---

## 9. Routes and scheduling

- Visits are the job's `scheduled_start_time/_end_time` (appointments module off). Reschedule via
  `PUT /jobs/{uid}/update` type SCHEDULE.
- Route per technician per day "<Technician> - <Day>", stops in appointment order, rebuilt within a
  minute of any job change; whose route = the technician assigned LAST among assignees on the
  Technician list; skips Paid/Cancelled/Reschedule Required; 60 days ahead; boards Retail + both AHS
  (+ Leak repair, Repair Service). WARN lines: scheduled with no technician; visit column with no time.
- `jobs.enforce_sequential_execution=false`; `jobs.notify_user_on_assignment` on (staff).
- `assign_sync.py` (manual) aligns assignees with the Technician dropdown.

---

## 10. CRM side (ghl-clone) relevant to Owen AI

- Zuper is the source of truth; the CRM mirrors one-way (`ZUPER_PULL_ONLY=true`), 15-min sweep, no
  webhook. The CRM's AHS pipeline is still the pre-2026-09-25 one (`mirror --phase boards` never run).
- Dispatch page (office to-do), Check Zuper (rule-based brief per job), scheduling plans, Collections,
  Insights, inspection-docs agent, post-inspection report drafts, tech briefing, weather — all read Zuper.
- Agent registry (AI Agents → Overview): every agent/action has a level Never → Shadow → Approve each
  → Automatic; more freedom needs an ADMIN + reason + typed PROMOTE. Owen AI should be registered the same way.
- Dispatch phase-3 writes (field, note, booking, own-board stage, address) built behind
  `DISPATCH_ZUPER_WRITES` — switched off again 2026-10-13.

---

## 11. Test records (safe targets; owner's own contact details)

- Job **#701** `6abd527f-31a6-43e9-bb0a-6fe557dac596`, AHS - Inspection, the developer's "(TEST)" customer
  `2dbd7187-a6e8-4452-8600-c3d029599d75`; reset to Work Order Received with fields cleared 2026-10-27.
- Job **#734** `2f14b7ff-7f94-4cc1-8aa2-dd62b067a6e7`, AHS - Inspection, the owner's "Test" customer `9568609a-c6ca-4d9a-a882-b3ef858c6c76`.
- Board **AHS - TEST** for rehearsing settings. Test jobs show up in counts and route WARNs.
- The test customers' phones are real phones of the owner/developer — a text to them is a real text.
  An old number once on the test customer is NOT ours — never use any number not confirmed in `live-account.md`.
- #694 was deleted (404).

---

## 12. Open items (as of the latest docs)

Run `mirror --phase boards` for the CRM; register a CRM webhook (fix event names); container
`zsafety.py` copies vs dispatch's; Retail/regional tasks and KPI questions; Leak repair board has no
columns; invoice payment terms; proposal email wording; contract PDF page 1/14; store the 1–5 rating;
in-Zuper staff alerts (bell step broken); 21 callbacks without an original job; data fixes in §7.4.

## 13. Doc discrepancies found while building this

- `docs/ZUPER-OPERATIONS.md` (main, 2026-10-26) is newer than the `ghl-clone` uncommitted copy (that is
  an old 2026-09-30 baseline with backports — fully superseded) but OLDER in places than the
  `docs/zuper-2026-10-12-evening` branch, which alone has: `ZUPER-PROPOSALS-AND-UPSELLS.md`, the
  workflow/webhook API rows, "checklist answers must be strings", the UNASSIGN body,
  `jobs.auto_add_task_assignee`, and the detailed lift list. That branch is older on state (TEST_MODE,
  repair video required). It was never merged.
- Main's docs say the customer-text workflows are LIVE ("six"; the welcome workflow makes seven) — memory
  says all 7 were deactivated 2026-10-26, along with the CRM auto-reply/dropped-call texts.
- §3.1 of ZUPER-OPERATIONS lists AHS - Repair & Review as 16 columns; 2026-10-27 changes made it 18 and reordered it.
- Inspection Completed has 21 questions since 2026-10-21 (docs say 18); job fields 84 (docs 79).
- `ZUPER-AUTOMATIONS.md` row K (LIVE) and §5 numbering are stale; `ZUPER-KPI-SETUP.md` omits the third Invoice Submitted question.
- `commission-rules.csv` is not in any repo (server-only); defaults are in code.
