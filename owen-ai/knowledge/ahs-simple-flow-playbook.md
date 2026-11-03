# AHS Simple Flow — operating playbook and audit rules

Built 2026-11-03 from the repo knowledge files (`zuper/live-account.md`, `zuper/operations.md`,
`zuper/api-reference.md`, `quo/api-reference.md`, `backend/app/dispatch/config.py` + `rules.py`,
`docs/ZUPER-*.md`, `DECISIONS.md`) **and a read-only live probe on 2026-11-03** (≈45 Zuper GET /
Connect-search reads and 6 Quo GETs, nothing written or sent; raw JSON on owen-main in
`/root/owen-ai-recon/sf/`). Customers appear by job number only.

Used by the `job-audit` skill. Order of reading: §0 (data map) → the stage in §1 → §2 holds →
the rules for that stage in §3 → §4 evidence rules before raising anything.

**Conventions.** ET = America/New_York. **Business time** = Mon–Sat 08:00–18:00 ET (CRM
`dispatch/config.py`); **bm** = business minutes, **BD** = business day. Deadlines count business
time unless they say "calendar". "(default — owner to confirm)" = no CRM SLA exists, the number is
ours. **Severity:** `urgent` = a customer is being hurt now or an SLA the owner set is broken;
`today` = must be fixed before the end of this business day; `watch` = hygiene / drift, list it.

---

## 0. What the data actually gives a rule (verified live 2026-11-03)

| Need | Where | Live finding |
|---|---|---|
| Current stage | `GET /jobs/{uid}` → `current_job_status{status_uid,status_name,status_type}` | 2 jobs on the board carry a status Simple Flow does not have (`Estimate Declined`, #275 #344): category was changed, status kept. Treat any non-SF status as **closed/foreign**, not as a stage |
| Stage history + timestamps | `job_status[]` (also on list rows): `status_name`, `status_uid`, `created_at` (= entered), `time_on_status` (minutes, filled when it LEFT; `null` on the current one), `done_by{first_name…}` (full GET only), `remarks`, **`category{category_name}`** | **History is kept across boards.** Every migrated job shows its whole AHS - Inspection / AHS - Repair & Review path, then `Got a job from AHS` → target stage on **2026-11-01** (or 11-02/11-03), done by Owen (some by Luis), seconds apart. Use `category` to tell old-board steps from SF steps |
| Days in stage | now − `job_status[-1].created_at` | For migrated jobs this resets on 11-01..11-03. Also report the last **old-board** status and since when (see §4.5) |
| Checklist answers | `job_status[i].checklist[{question,answer,type,updated_at}]` on the entry for that stage | **Every SF checklist entry on all 61 jobs is EMPTY (0 answers).** Entering via category change adds no checklist (61 of 62 `Got a job from AHS` entries have none); moving into stages 3–7 attaches the questions with `answer:null`. No SF question is required or linked to a field. ⇒ checklist = **can't verify** until the team starts answering; never treat an empty checklist as proof a step was skipped |
| Intake answers | job `custom_fields` group **Checklist** (`What's happening there?`, `How many leaks?`, `Where is the leak located inside the house?`, `How old is the roof?`, `Any previous repair?`, `Roof material`, `How many stories?`, `Does the customer live there, or are there tenants?`, `When did the leak start?`, `First time using AHS for roofing?`, `Service address verified`, `Email verified`, `Second phone number`, `Preferred inspection day & time`, `Explained the AHS repair process`, `Introduced Antonio`) | Filled on most migrated jobs by the OLD Welcome Call! checklist (copy-to-field). The SF intake checklist is NOT linked to these fields, so new SF jobs will only have them if typed by hand or written in notes |
| AHS / money fields | `AHS Job ID` (filled on ~33+), `AHS authorized ($)`, `Customer chose`, `Customer paid ($)` (only from old-board KPI checklists), `Technician`, `CRM Opportunity ID`/`CRM Link` | SF has no KPI questions; these stay empty on jobs that never passed the old KPI columns |
| Visit | `scheduled_start_time` / `scheduled_end_time` (UTC) — the only visit record (appointments module off) | ⚠ Several upcoming visits sit at **11:30Z = 06:30 ET** (DST ended 11-01; booked as 07:30 EDT). One customer was told "7:30–10 AM". Convert to ET and treat a visit before 07:00 ET as "time probably shifted 1 h" (rule SF-DATA-03) |
| Who | `assigned_to[{user{first_name},assigned_at}]`; field `Technician` | Assignees are a mix (Owen 26 / Antonio 18 / Luis 13 on 11-02). Luis is often assigned as office owner |
| Line items | `products[]` (`product_id`, `total`) | `AHS-LEAK-REPAIR` 1125, `DTR-CUSTOM-GOOD/BETTER`, `AHS-TILE-01`… (signed option copied by the CRM) |
| Notes | `GET /jobs/{uid}/note?count=100` → `note` (HTML), `note_type` TEXT/IMAGE/VIDEO, `attachments[]`, `created_by`, `created_at` | Text IS returned. The first note on CRM-created jobs (the AHS work order) has **no text**. Office writes inspection reports, "customer needs repair before Nov 4th, accepted $750 NCC", "we need to go back…" here; tech posts IMAGE/VIDEO notes (video notes carry a transcript as text) |
| Status-move remarks | `job_status[i].remarks` | Used for facts: "AHS paid per the Frontdoor portal - owner approved 2026-11-02" |
| Proposal | `GET /estimate?filter.job=<uid>` (list row is enough) | Rows carry **`estimate_status`, `estimate_date`, `sent_date`, `accepted_date`, `customer_viewed`, `deposit{total,collected,status}`, `is_converted`, `proposal_options[{option_name,is_accepted}]`, `total`**. `status_history` is empty on the list row (full GET has it) |
| Customer invoice + payment | `GET /invoice?filter.job=<uid>` | `invoice_status`, `total`, `amount_paid`, `amount_due`, `is_paid`, `paid_date`, `payment_history[]`. Customer money only. **AHS's invoice/payment is in Frontdoor, not Zuper** |
| Commission | `GET /commissions?filter.job_uid=` | exists per job when recorded |
| Zuper Connect calls | `POST {connect}/calls` with `filters.customer_uid:[uid]` (works) or `filters.module_uid:[job_uid]` (works but **misses calls not linked to the job** — 1 of 2 for #757). List has no summary text | Use `customer_uid` |
| Zuper Connect texts + call summaries | `POST /message/conversations {filters:{status:"",keyword:"<10 digits>"}}` → **one conversation per our line** (7373 and 0190 both seen) → `POST /messages {filters:{conversation_uid,message_type:[SMS,MMS,CALL,SYSTEM]}}` | Newest first. CALL rows carry `call.status`, `call.duration` and **`call.call_recordings[0].call_summary{summary,next_action[],sentiment}`** — the best promise/price source. `user.first_name` = the Zuper login, **not necessarily the person** (Marienne, office, works under Luis's login; the AI summary even misnames her) |
| Quo calls / texts | `GET /calls` and `GET /messages?phoneNumberId=PNRxH5G3uI&participants=%2B1XXXXXXXXXX&maxResults=50` | **Busy**: 13 calls + 7 texts on one job, 6 + 15 on another. Every outgoing row is user `US5MlHABW5` (Owen's) even when Marienne/office sent it — Quo cannot tell who. Summaries/transcripts: `GET /call-summaries/{id}`, `/call-transcripts/{id}` |
| Same message twice | — | Office texts can appear in BOTH Connect (7373) and Quo within ~1 min. De-duplicate by text + time ±2 min |
| System texts | Connect OUTGOING labelled "Owen"/"Zuper" with template wording (welcome, "Reminder:", "on schedule to arrive today") | All 7 customer-text workflows OFF since 2026-10-26 — so **no automatic reminders exist now**; confirmations must be human |

**Not in any API:** AHS Frontdoor (claim submitted, authorization, AHS invoice, AHS payment) except
the AHS note emails owen-main turns into CRM "AHS approved" items; GHL (named in the Handoff
checklist — means the CRM/text tools); Zelle/check payments unless the office records them on
the proposal deposit or invoice; meetings held in person (Handoff Meeting); Google reviews
posted; expenses/job costing unless entered (`GET /expenses` docs-only); CompanyCam photos (field
`CompanyCam Project` only).

**Live board on 2026-11-03 (61 jobs):** Got a job from AHS 16 · Day of Inspection **0** · Proposal
Creation 4 · Handoff Meeting 10 · Repair Day 11 (3 are REVISIT callbacks, created directly on SF)
· Quality Call 1 · Job Completed 17 · foreign status 2. **How the team uses it:** a booked
inspection stays in *Got a job from AHS* (nobody uses Day of Inspection yet); a booked repair sits
in *Handoff Meeting* or *Repair Day* days ahead. ⇒ Rules key on **dates and evidence**, not on the
stage being moved on time; a stage lagging the facts is only `watch` (SF-HYG-01).

---

## 1. The lifecycle (7 stages)

Board `AHS Simple Flow` `2fbc07ec-94e0-43ca-98d5-c902f12b59e5`. People: **Office** = Luis
Candiales (office manager, Zuper login "Luis") with Marienne (CSR; texts/calls under Luis's Zuper
login and Owen's Quo user); **Antonio** Brown = field tech (most repairs); **Owen** = owner, also
inspects; approves change orders and prices.

### Stage 1 — Got a job from AHS · `c587fb9b-f637-4391-b85e-12dd6d048d03` · NEW
| | |
|---|---|
| Purpose | AHS work order in → first contact → welcome call + intake → inspection booked |
| Owner | Office |
| Must happen | First contact ≤ 120 bm of creation; keep calling every BD until reached; welcome call with the pitch; intake answers; inspection date + tech set; customer told the window |
| Checklist (15, none required, not linked) | 1 What is happening? · 2 Leaks and location · 3 Roof age · 4 Previous repairs · 5 Roof material · 6 Number of stories · 7 Does the customer live there? · 8 When did the leak start? · 9 First time using AHS for roofing? · 10 Service address confirmed · 11 Second phone number · 12 Email address · 13 Preferred contact method · 14 Appointment day and time (DATETIME) · 15 HEADER "Welcoming Call Pitch" |
| Pitch (header text, summary) | Thanks; how AHS works: we inspect and document what failed → submit to AHS → once AHS approves we send an agreement → **contract signed puts you in the queue** → scheduled; watch notes and email; anything above scope (broken tiles, missing shingles) we tell you and you decide; then the intake questions; confirm address, second phone, email, preferred contact method, inspection day/time; recap |
| Exit | Inspection date in `scheduled_start_time` + tech assigned → (should) move to Day of Inspection on the day |
| Absorbed old columns | Work Order Received, Not Answering -Keep Call!, Welcome Call!, Scheduled, Inspection: Day-Before Cal (as the job's waiting state; the call itself is now chat evidence), Reschedule Required / Waiting for Customer (pre-inspection) |

### Stage 2 — Day of Inspection · `b1255be9-7d4b-42a4-b5fe-fda80259bb34` · SCHEDULED
| | |
|---|---|
| Purpose | Same-day confirmation, on-the-way, the inspection, documentation, **AHS claim submitted** |
| Owner | Tech (Owen/Antonio) on site; office for same-day confirmation and Frontdoor submission |
| Must happen | Same-day confirmation/ETA; on site; photos/videos; findings recorded; call the customer after; claim/authorization report submitted to AHS in Frontdoor |
| Checklist (15, display order) | 1 Scope recorded · 2 Materials and tools list recorded before leaving · 3 Album 2 - 360 degree roof photos (MULTI_IMAGE) · 4 Many roof detail photos (in-app camera, geo-stamped) (MULTI_IMAGE) · 5 2-3 short videos (issue, recommendation, urgency) (VIDEO) · 6 Urgency recorded · 7 Recommendation recorded · 8 Album 3 - leak location photos inside AND suspect area above (MULTI_IMAGE) · 9 Album 1 - 360 degree house photos from ground (MULTI_IMAGE) · 10 How long signs have been present recorded · 11 Photo of EVERY failure point (MULTI_IMAGE) · 12 Acute vs pre-existing reasoning recorded · 13 Leak signs and location recorded · 14 Visible failure documented · 15 Submit claim/authorization report to AHS in Frontdoor |
| Exit | Inspection documented + AHS submission done → waiting on AHS (HOLD H4) → authorized → Proposal Creation |
| Absorbed | Inspection: Same-Day Confi, Antonio On the Way, Inspection In Progress, Inspection Completed, Submit To AHS For Approval, Awaiting AHS Decision (as a hold), AHS Approved (as an event) |

### Stage 3 — Proposal Creation · `cea9574b-8939-427c-8849-1a5898681e3e` · ESTIMATING
| | |
|---|---|
| Purpose | Price, build Good/Better/Best, send, get the choice, signature and 50% deposit |
| Owner | Office (Luis builds proposals), Owen approves prices |
| Must happen | Price meeting; DTR proposal created **from the job**; sent (status AWAIT_RESPONSE, `sent_date`); followed up; option accepted (APPROVED, `accepted_date`); 50% deposit collected (`deposit.status` COLLECTED) unless the chosen option is $0 |
| Checklist (7) | 1 Total Squares (added 2026-11-03) · 2 Price meeting - decide AHS-covered / non-covered / upgrade · 3 Good/Better/Best built with terms, prices, warranties, scope · 4 Agreement sent · 5 Option chosen · 6 Document signed · 7 50% deposit paid |
| Exit | Signed + deposit (or $0 option signed) → Handoff Meeting |
| Absorbed | AHS Approved (booking side), Proposal Made, Waiting for Customer (proposal side), Estimate Declined (no column now — HOLD/closure H5) |
| Note | No CPQ trigger on this board (templates fire only on AHS - Inspection / Proposal Made) |

### Stage 4 — Handoff Meeting · `4492c949-deac-41d2-8366-f00a9f3d2420` · SCHEDULED
| | |
|---|---|
| Purpose | Office → crew handoff; materials; date; tell the homeowner |
| Owner | Office + Antonio; Owen approves change orders |
| Must happen | Scope/photos/estimate match; materials on hand/ordered; Antonio briefed; crew, date, start time; homeowner told the window; change-order rule; dry-in plan |
| Checklist (7) | 1 Photos, scope, and estimate match — if not, job does not start · 2 Materials confirmed on hand or ordered — if not, job does not start · 3 Antonio talked through the job: scope, materials, photos, and what to watch for · 4 Crew, date, and start time assigned · 5 Homeowner told the window from GHL · 6 Change orders approved BEFORE writing — out-of-scope work stops until Owen approves · 7 Dry-in same day plan confirmed — never open more roof than can be closed the same day |
| Exit | Repair date set, customer told, day-before confirmation → Repair Day on the day |
| Absorbed | Repair Scheduling Call, Day-Before Call (repair), Reschedule Required / Waiting for Customer (repair side), Not Answering- Keep Call! (repair side) |

### Stage 5 — Repair Day · `ea189583-9cca-4c10-9ab6-7a4e8e7a4c05` · STARTED
| | |
|---|---|
| Purpose | Do the repair to scope, document, customer sign-off |
| Owner | Antonio (crew) |
| Must happen | On-the-way/ETA to customer; before/during/after photos; work to scope; sign-off; walkthrough; tile quality checks |
| Checklist (15) | 1 Scope confirmed with customer · 2 Photos taken before work starts (MULTI_IMAGE) · 3 Job notes read · 4 Tools on truck · 5 Materials on truck · 6 Customer sign-off obtained (SIGNATURE) · 7 Walkthrough with customer done · 8 Photos after work (MULTI_IMAGE) · 9 Photos during work (MULTI_IMAGE) · 10 Work complete per scope · 11 Never more roof opened than closed same day · 12 Any tile cracked while working replaced · 13 All tiles flat and interlocked · 14 No lifted corners · 15 No exposed nail heads |
| Exit | Work complete → Quality Call. Callbacks (REVISIT jobs) also run through here |
| Absorbed | On the Way to Repair, Repair In Process, Repair Complete, Callback in Progress |

### Stage 6 — Quality Call · `a4906cd6-4571-4a84-b8b7-a709c0143aa4` · FOLLOW_UP
| | |
|---|---|
| Purpose | Satisfaction call; still leaking?; review only if happy |
| Owner | Office (Antonio asks on site) |
| Checklist (8) | 1 Repair good? · 2 Roof still leaking? · 3 Everything working? · 4 Happy? · 5 If happy: send Google review link · 6 Antonio asks for Google review while on site · 7 If not happy: do NOT push a review · 8 Note issue, owner, and date to fix |
| Exit | Happy → review link → Job Completed; not happy / leaking → callback REVISIT job (HOLD H7) |
| Absorbed | Call Satisfaction Check, Review Requested, Review Received, Call Back |
| Policy conflict | Checklist says review link **only if happy**; DECISIONS 2026-10-22 (reviews agent #9) offers the link to every responder (anti-gating). Audit what the checklist says; flag the conflict in §6 |

### Stage 7 — Job Completed · `d2caa95b-0f1d-4c1b-86ce-d16507bc6801` · COMPLETED
| | |
|---|---|
| Purpose | Close the money and the paperwork |
| Owner | Office; Owen for costing/commission |
| Checklist (8) | 1 Invoice matches estimate/scope · 2 AHS invoice separate from customer invoice · 3 Expenses recorded · 4 Job costing within 48h (labor + materials vs estimate) · 5 Technician commission calculated and recorded · 6 Payment collected / AHS payment tracked · 7 Warranties sent · 8 6-month reactivation tagged |
| Exit | Nothing open: customer invoice PAID, AHS paid, commission recorded. There is **no Paid/closed column** — a Job Completed job with money still open is a hold (H6) |
| Absorbed | Invoice Submitted to AHS, Awaiting AHS Payment, Paid |

**Cancel / decline:** Simple Flow has no Cancelled or Estimate Declined column. A cancelled or
declined job either keeps an old closed status (#275, #344) or sits in its last stage. Recognise it
from notes/remarks/chat ("cancel", "declined", "not interested", "went with another company")
and report it once as SF-HYG-04, not as a stale job.

---

## 2. HOLD situations — how to recognise them now and what "being worked" means

| # | Hold (old column) | Recognise now (all must be read together) | "Being worked" = | Escalate when |
|---|---|---|---|---|
| H1 | Not answering (Not Answering -Keep Call!, Not Answering- Keep Call!) | Stage 1 (or 4 for repair booking), **no `talked` contact and no inbound text** since creation / since the last booking need; ≥1 outbound attempt exists | ≥1 outbound attempt (call any duration, or text) **every BD**; day 1: 2 attempts incl. 1 text (default — owner to confirm) | 3 BD with no reach → `today` (text + voicemail + check 2nd phone/email); 5 BD → `urgent`, tell Owen / AHS (default) |
| H2 | Waiting for Customer | Last message is OURS asking something (date, decision, payment, access) and no customer reply; or note/summary "customer will call back / check with spouse / out of town until …" | A follow-up every 2 BD (default), or on the date the customer gave | No follow-up 3 BD after our last ask, or the customer's date +1 BD passed |
| H3 | Reschedule Required | `scheduled_start_time` in the past AND no visit evidence that day (no IMAGE/VIDEO note, no tech call/text, no move to the next stage); or chat says "reschedule/rain/can't make it"; or note "standby" | New future date set within 1 BD (CRM "book" 600 bm), customer told | 1 BD with no new date → `today`; 3 BD → `urgent` |
| H4 | Waiting on AHS authorization (Submit To AHS For Approval, Awaiting AHS Decision) | Inspection done (visit date passed + photos/report note) and no authorization evidence: no `AHS authorized ($)`, no `AHS-LEAK-REPAIR` line, no proposal, no chat/note "authorized/approved/AUTHO #" | Customer updated every 3 BD (default); AHS chased at 3 BD (can't verify — Frontdoor) | No customer contact 3 BD → `today`; 7 days with no authorization → `today` "chase AHS" (CRM stale = 7 d) |
| H5 | Waiting on signature / deposit (Proposal Made, Estimate Sent, Follow Up) | Proposal `AWAIT_RESPONSE`/`AWAIT_SIGNATURE`, or APPROVED with `deposit.status` ≠ COLLECTED and option total > 0 | Follow-up 1, 3, 7 BD after `sent_date` (default) | See SF-PROP-04/05 |
| H6 | Waiting on AHS payment / customer balance (Invoice Submitted to AHS, Awaiting AHS Payment, Collect Balance) | Job Completed (or Quality Call) and: customer invoice `amount_due` > 0, or AHS paid not evidenced (no remark/note "AHS paid") | Balance: reminder every 3 BD (default). AHS: check Frontdoor weekly (default) | Customer balance > 7 days → `today`; AHS unpaid > 30 days after repair → `today` (default) |
| H7 | Callback / still leaking (Call Back, Callback in Progress) | Customer says leaking / "still", "again", "water" after a repair; or a REVISIT job with `parent_job` exists and is open | Callback job created ≤ 1 BD, visit ≤ 3 BD (default), customer updated each BD until fixed | Any "still leaking" with no callback job in 1 BD → `urgent` |

A hold is **not** a failure by itself; the failure is a hold nobody is working (rule SF-HOLD-01).

---

## 3. Audit rules

Columns: **When** = deadline/threshold; **Detect** = exactly what to read (window); **Sev** =
severity when it fails. "talked" / "attempt" / "confirmed" are defined in §4.

### 3.1 Intake (stage 1)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-INTAKE-01 | New AHS job not contacted | 120 bm after `created_at` (CRM `new_ahs`) | No outbound call/text (Connect by `customer_uid` + phone keyword, Quo by phone) and no talked inbound after `created_at`. Ignore system welcome text | urgent | Call now; if no answer text, then follow H1 |
| SF-INTAKE-02 | Not-answering cadence broken (H1) | each BD after the first attempt | Customer never reached and a BD (08–18 ET, Mon–Sat) passed with **zero** outbound attempts | today (urgent at 5 BD) | Call + text today; try 2nd phone / email from fields |
| SF-INTAKE-03 | Reached but welcome call/intake not done | 1 BD after first talked call (default) | First talked call exists, but intake missing: none of the Checklist-group fields (`What's happening there?`, `How many leaks?`, `Roof material`…) filled AND no SF checklist answers AND no note with intake facts | today | Complete intake from the call (summary) into the job |
| SF-INTAKE-04 | Pitch / expectations not given | at welcome call | `Explained the AHS repair process` empty and no call summary mentioning AHS process/agreement/queue. Mostly **can't verify** — raise only if the customer later says they did not know about the agreement/deposit | watch | Re-explain process on next call |
| SF-INTAKE-05 | Inspection not booked | 600 bm (1 BD) after first talked call (CRM `book`) | Stage 1, `scheduled_start_time` null or in the past, no "standby/waiting for date" note | today | Book date + tech; tell customer |
| SF-INTAKE-06 | Booked but no technician | at booking | `scheduled_start_time` set, `assigned_to` has no tech (Owen/Antonio) and `Technician` empty (route WARN) | today | Assign tech (routes follow the `Technician` field) |
| SF-INTAKE-07 | Customer not told the inspection window | before the day-before call | After the date was set (`assigned_at`/history), no outbound text/call mentioning the day/time | watch | Text the window |

### 3.2 Inspection visit (stages 1→2)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-VISIT-01 | Day-before confirmation missing (inspection or repair) | by 18:00 ET of the BD before the visit (Mon visit → Sat) (default; CRM flags visits < 24 h with no contact in 3 days) | No **confirmed** contact (§4.2) from 08:00 ET of the previous BD until audit time. Check Connect + Quo. **No checklist field exists for it**; reminders are OFF | urgent if visit < 24 h; today otherwise | Call now to confirm; if no answer, text + tell tech |
| SF-VISIT-02 | Same-day confirmation / on-the-way missing | before `scheduled_start_time` on the day | No outbound call/text to the customer on visit day before arrival (tech or office). Antonio usually calls from Connect ("heading to the property") | watch | Tech calls/texts ETA |
| SF-VISIT-03 | Visit date passed, no sign it happened (H3) | 60 min after `scheduled_end_time` (CRM `NO_PHOTOS_AFTER_MINUTES`); then 1 BD | No IMAGE/VIDEO note, no tech call that day, no report note, stage not advanced; or a note "standby"/chat "reschedule" | today | Confirm with tech; reschedule and tell customer |
| SF-VISIT-04 | Inspection not documented | same day as visit, +30 min grace (CRM inspection-docs) | Missing any of: leak location, scope/recommendation, materials & tools, photos (IMAGE notes / albums `360 Roof`, `360 Ground`, `360 Attic`, `For the Owner` counts via `GET /attachments/folders`), video. Sources: Day of Inspection checklist, fields (`Leak location`, `Tools & materials needed`…), notes ("Inspection Report…") | today | Tech adds report/photos/video |
| SF-VISIT-05 | Customer not called after inspection | 120 bm after visit end / last photo (CRM `after_inspection`) | No talked call or text exchange after the visit | today | Call: explain findings, AHS next step |
| SF-VISIT-06 | Stage lag: visit done, still in stage 1 | 1 BD after visit | Visit evidence exists but stage = Got a job from AHS | watch | Move to Day of Inspection / next stage (person) |

### 3.3 AHS authorization (stage 2→3, hold H4)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-AHS-01 | Claim not submitted to AHS | 1 BD after inspection (default — owner to confirm) | No evidence of submission: checklist item 15 empty AND no note/remark/chat "submitted", "Frontdoor", "sent to AHS", AND no old-board `Submit To AHS For Approval` entry. Frontdoor is not readable ⇒ usually **can't verify**; raise only on positive evidence it was not done (e.g. chat "we still need to submit") | today | Submit in Frontdoor; note it on the job |
| SF-AHS-02 | AHS decision not chased | 3 BD after submission with no authorization (default); 7 d = CRM stale | H4 signals | today | Call/portal AHS; note result |
| SF-AHS-03 | Customer not updated while waiting on AHS | every 3 BD (default) | No outbound contact for 3 BD while in H4 | today | Text/call status update ("submitted / waiting on AHS") |
| SF-AHS-04 | Authorized but customer not called | 1 BD after authorization evidence (old customer text promised "within 1 business day") | Authorization evidence (CRM "AHS approved" item, note "authorized/AUTHO", `AHS authorized ($)` set) with no talked contact after it | urgent | Call to explain coverage + next step (proposal) |
| SF-AHS-05 | Wording rule broken | any time | Outbound text says AHS "approved" or quotes AHS amounts (rule: "authorized", never amounts in texts) | watch | Coach wording |

### 3.4 Proposal, signature, deposit (stage 3, hold H5)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-PROP-01 | Proposal not built | 1 BD after authorization (default) | `GET /estimate?filter.job=` returns none (non-deleted) while job is past authorization / in Proposal Creation | today | Build DTR proposal from the job |
| SF-PROP-02 | Proposal built but not sent | 1 BD after `estimate_date`/created (default). Live: #757 built 10-29, sent 11-02 | status DRAFT, `sent_date` null | today | Owen approve → send |
| SF-PROP-03 | Proposal not created from the job / off-convention | at creation | Proposal with no `job`, or lines not `DTR-…`/`UPG-…`/`AHS-…` codes, or deposit ≠ 50% of option total (live: 50 of 59 at 50%, 8 at 100%) | watch | Fix per proposal rules (`operations.md` §7.1) |
| SF-PROP-04 | Proposal follow-up missed | 1, 3 and 7 BD after `sent_date` with no acceptance (default — owner to confirm) | AWAIT_RESPONSE/AWAIT_SIGNATURE and no outbound contact in the window; `customer_viewed:false` makes it more urgent | today (urgent at 7 BD) | Call: walk through options |
| SF-PROP-05 | Proposal about to expire | 5 days before `expiry_date` (30 d default) | still not accepted | watch | Follow up or re-issue |
| SF-PROP-06 | Signed but deposit not collected | before Handoff / repair date set; at latest 2 BD after `accepted_date` (default) | APPROVED, accepted option total > 0, `deposit.status` ≠ COLLECTED (or `collected` < `total`). Zelle/check promised in chat but not recorded = still open (note what was promised) | today (urgent if repair date ≤ 1 BD away) | Collect / record the deposit on the proposal |
| SF-PROP-07 | Work booked before signature | when a repair date exists | `scheduled_start_time` after the inspection for a repair, but no APPROVED proposal (and not a $0 AHS-only job with signed agreement) | urgent | Get signature first (pitch: "contract signed puts you in the queue") |
| SF-PROP-08 | Signed option not on the job | 1 BD after `accepted_date` (CRM copy runs every 15 min) | Job `products[]` lacks the accepted option's `DTR-…` line | watch | Check CRM proposal copy / add line |

### 3.5 Handoff and scheduling (stage 4)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-HAND-01 | Repair not scheduled | 1 BD (600 bm) after signature+deposit (CRM `book`) | APPROVED (+deposit) and no future `scheduled_start_time` | today | Book date + crew |
| SF-HAND-02 | Handoff meeting not evidenced | before repair date | No checklist answers, no note "handoff/briefed", no call between office and Antonio. **Usually can't verify** — list it as can't verify; raise only if the repair went wrong for a briefing reason (wrong materials, wrong scope) | watch | Hold the 10-minute handoff; note it |
| SF-HAND-03 | Materials not confirmed | before repair date | Tech/customer chat or note "need to order", "didn't have", repair postponed for materials | today | Order/confirm, re-confirm date |
| SF-HAND-04 | Crew/date/time missing | at stage 4 | Stage 4 and (`scheduled_start_time` null or no tech assigned) | today | Assign |
| SF-HAND-05 | Homeowner not told the repair window | 1 BD after date set, and before day-before | No outbound message/call after the date was set that mentions the day/time | today | Tell the window (text) |
| SF-HAND-06 | Change order without Owen's approval | when out-of-scope work appears | Chat/summary: tech agrees extra work/price on site; no Owen approval (note/chat) and no proposal change | urgent | Stop; Owen approves; update proposal |
| (SF-VISIT-01/02 apply to the repair visit too) | | | | | |

### 3.6 Repair day (stage 5)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-REP-01 | Repair visit not evidenced | 60 min after `scheduled_end_time` | No IMAGE/VIDEO notes that day, no tech contact, no update | today | Check with Antonio; reschedule if needed (H3) |
| SF-REP-02 | Photos before/during/after missing | end of repair day | Fewer than 3 photo moments (IMAGE notes spread over the visit, or the 3 MULTI_IMAGE answers). Gallery count can exceed API count — if a few exist, don't flag | today | Tech uploads photos |
| SF-REP-03 | Customer sign-off / walkthrough missing | end of repair day | SIGNATURE answer empty AND no chat/summary of walkthrough. Checklist empty everywhere today ⇒ **can't verify** unless chat shows the customer was not shown the work | watch | Get sign-off on next contact |
| SF-REP-04 | Repair done, stage not advanced | 1 BD after visit (default) | Visit evidence + still Repair Day | watch | Move to Quality Call |
| SF-REP-05 | Roof left open / dry-in breach | any | Chat "tarp", "left open", "come back tomorrow to close", rain complaint | urgent | Owen calls the customer |

### 3.7 Quality call and review (stage 6)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-QC-01 | Quality call not made | end of the next BD after the repair (CRM Q6) | No talked call / text exchange with the customer after repair day asking how it went | today | Call: repair good? still leaking? happy? |
| SF-QC-02 | Review asked from an unhappy customer | any | Review link / "Google review" sent after negative words or a leak report, before it was fixed | today | Stop review push; fix first |
| SF-QC-03 | Happy customer, no review ask | 2 BD after a positive quality call (default; agent #9 asks at Repair Complete +2 d) | Positive answer, no review-link message and no note that Antonio asked on site | watch | Send the official review link |
| SF-QC-04 | Still leaking → no callback (H7) | 1 BD to open a REVISIT job; 3 BD to visit (default) | Leak words after repair and no REVISIT job with `parent_job` = this job (`GET /jobs?filter.parent_job=`), or callback job with no date | urgent | Create callback job, book visit, tell customer |
| SF-QC-05 | Issue noted without owner/date | at quality call | Problem raised but no note naming who fixes it and by when (checklist 8) | watch | Note issue, owner, date |

### 3.8 Completion, money, paperwork (stage 7, hold H6)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-PAY-01 | Customer invoice missing | 1 BD after repair (default) | Accepted option total > 0 and `GET /invoice?filter.job=` empty (or proposal not `is_converted`) | today | Convert proposal to invoice |
| SF-PAY-02 | Invoice ≠ accepted option | at invoice | `invoice.total` ≠ accepted option total (known past case #274 $375 vs $1,500) | today | Correct invoice (owner OK) |
| SF-PAY-03 | Customer balance open | 7 calendar days after repair (default) | `amount_due` > 0; no reminder in the last 3 BD | today | Collect; remind |
| SF-PAY-04 | AHS invoice not submitted | 2 BD after repair (default) | No evidence: no `AHS-LEAK-REPAIR`/`AHS-TRIP`/`AHS-ADDL-LEAK` line on the job, no note/remark "invoiced AHS". Frontdoor unreadable ⇒ mostly can't verify; the missing AHS line IS verifiable | today | Add AHS lines; submit in Frontdoor |
| SF-PAY-05 | AHS payment not tracked | 30 days after repair (default) | No remark/note "AHS paid" (live example: remark "AHS paid per the Frontdoor portal…") | watch → today at 45 d | Check Frontdoor; record |
| SF-PAY-06 | Job costing not done in 48 h | 48 h calendar after Job Completed entry (checklist wording) | Checklist item 4 empty and no `GET /expenses` rows for the job (docs-only endpoint) ⇒ **can't verify** today | watch | Record labor + materials vs estimate |
| SF-PAY-07 | Commission not recorded | 7 days after Job Completed (default) | `GET /commissions?filter.job_uid=` empty for the tech on a job with lines | watch | Record commission (Owen) |
| SF-PAY-08 | Warranty not sent / 6-month reactivation not tagged | 2 BD after completion (default) | No outbound message/email mention of warranty; no tag/note. Mostly can't verify | watch | Send warranty; tag |
| SF-PAY-09 | Job Completed with work still open | at entry | Job Completed but: open REVISIT child, unanswered leak complaint, `amount_due` > 0, or no repair evidence | today | Work the open item (callback / complaint / balance); don't treat the job as closed |

### 3.9 Stale jobs (any stage) — days in stage, measured per §4.5

| ID | Stage | Threshold (default — owner to confirm unless marked CRM) | Sev |
|---|---|---|---|
| SF-STALE-01 | Got a job from AHS, no visit date | 1 BD (CRM `book`) | today |
| SF-STALE-02 | Got a job from AHS, visit booked | visit date + 1 BD | today |
| SF-STALE-03 | Day of Inspection | 2 BD after visit without authorization or update | today |
| SF-STALE-04 | Proposal Creation | 5 BD without being sent, or 10 BD sent-not-accepted | today |
| SF-STALE-05 | Handoff Meeting, no repair date | 3 BD | today |
| SF-STALE-06 | Handoff Meeting / Repair Day with a date | repair date + 1 BD | today |
| SF-STALE-07 | Quality Call | 3 BD | watch |
| SF-STALE-08 | Job Completed with money/AHS open | 30 days | watch |
| SF-STALE-09 | Any stage, no conversation and no Zuper change | 7 calendar days (CRM `STALE_DAYS`) | watch (CRM never makes quiet urgent) |

### 3.10 Conversations (any stage)

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-CHAT-01 | Missed inbound call not returned | 30 bm (CRM `missed`) | Inbound call missed (Quo `missed/no-answer/abandoned` or `answeredAt` null; Connect `MISSED`/`VOICEMAIL`) with no later outbound call/text or talked call | urgent (≤ 7 days old), watch if older (CRM) | Call back |
| SF-CHAT-02 | Customer text not answered | 30 bm | Last inbound text has no later outbound and is not an ack ("ok", "thanks", "👍", "yes"… — CRM `ACKS`); Connect `is_unresponded:true` helps | urgent if a question/request; today otherwise | Reply |
| SF-CHAT-03 | Promise not kept by its date | the promised date/time (+2 h grace in business time) | From our side in texts, call summaries (`next_action[]`), notes: "we'll send…", "Antonio will be there between…", "we'll call you tomorrow", "we'll register the payment". Find the matching evidence after it (message, visit, record). Quote it | urgent if customer-facing and past; today otherwise | Do it / tell the customer |
| SF-CHAT-04 | Price/scope agreed in chat not in Zuper | 1 BD after the conversation | Amount or option agreed in a call summary/text (e.g. "selected the basic $750 option") ≠ accepted proposal option total / job line `total`; or extra work agreed with the tech without a proposal change | today (urgent if the repair is next) | Update proposal / lines (Owen approves) |
| SF-CHAT-05 | Customer upset / complaint | immediately | Words: complaint, frustrated, "nobody called", "still waiting", "terrible", "bad review", "cancel", "lawyer", "AHS complaint", repeated "?" messages; Connect `sentiment: NEGATIVE` | urgent | Owen or office calls today |
| SF-CHAT-06 | Talked, Zuper not updated | 120 bm after the talk (CRM `talked_not_updated`) | Talked call newer than the last stage move and last note, and the call changed something (date, decision, payment) | watch | Record it in Zuper |
| SF-CHAT-07 | Customer asked something nobody answered in a call | 1 BD | `next_action[]` item owed by us with no follow-up | today | Answer |

### 3.11 Board hygiene / data

| ID | Failure | When | Detect | Sev | Fix |
|---|---|---|---|---|---|
| SF-HYG-01 | Stage lags the facts | 1 BD | Evidence shows a later step done (e.g. repair photos while in Handoff) | watch | Move the stage |
| SF-HYG-02 | Open job still on an old AHS board | now | `GET /jobs?filter.category=f07e3c01…` (AHS - Inspection) / `2d0160ea…` (AHS - Repair & Review) with a status not in Cancelled/Estimate Declined/Paid/Callback Done/Review Received. Live 11-03: **8 open on AHS - Inspection** (#787, #794–#797 new work orders, #756 Submit To AHS, #413 Not Answering, #701 TEST) and **8 open on Repair & Review** (#759, #764, #318, #656, #355, #703, #724, #285) | today (new work orders) / watch | Move to Simple Flow (category + stage) — person |
| SF-HYG-03 | New AHS email job lands on AHS - Inspection | each new job | The CRM `ahs_create` still targets AHS - Inspection (verified: 5 new jobs 11-02/11-03) | today | Move it; ask dev to change the target board |
| SF-HYG-04 | Cancelled/declined job not closed | at the decision | Chat/note cancel words, or a foreign closed status on the board (#275, #344) | watch | Decide where closed jobs go (no column) |
| SF-HYG-05 | REVISIT without parent / callback not linked | at creation | `job_type: REVISIT` with `parent_job` null | watch | Link the original job |
| SF-DATA-01 | Visit in a stage that needs none, or none where one is needed | now | Stage 4/5 with no date; or stage 1 with a date in the past (→ H3) | today | Fix date |
| SF-DATA-02 | Scheduled with no technician | now | as SF-INTAKE-06 for any visit | today | Assign |
| SF-DATA-03 | Visit time outside 07:00–18:00 ET | now | `scheduled_start_time` in ET < 07:00 or ≥ 18:00 — seen at 06:30 ET after the DST change | today | Confirm the real time with the customer; fix |
| SF-HOLD-01 | A hold (§2 H1–H7) nobody is working | the hold's "Escalate when" column | The hold's signals + no "being worked" evidence in its window | per §2 row | The hold's action |
| SF-OTHER | Anything else the evidence shows is wrong | — | must quote evidence | judge | — |

**Count: 77 rules** (SF-INTAKE 7, SF-VISIT 6, SF-AHS 5, SF-PROP 8, SF-HAND 6, SF-REP 5, SF-QC 5,
SF-PAY 9, SF-STALE 9, SF-CHAT 7, SF-HYG 5, SF-DATA 3, SF-HOLD-01 — "a hold (§2) nobody is working",
use the hold's escalation row — and SF-OTHER).

---

## 4. Evidence rules

### 4.1 Gathering
- **Customer phones:** `customer.customer_contact_no.mobile/phone/home/work` + field `Second phone number`. Normalise to the last 10 digits; Quo needs `+1XXXXXXXXXX`.
- **Zuper Connect:** `/calls` with `customer_uid` (not `module_uid` alone) + `/message/conversations` keyword = 10 digits → **every** conversation returned (one per line) → `/messages`. Window: from 2 days before `created_at` (CRM `COMMS_DAYS` = 21 for the rolling view).
- **Quo:** `/calls` and `/messages` with `phoneNumberId=PNRxH5G3uI` per phone; summaries via `/call-summaries/{id}`.
- **Zuper record:** full `GET /jobs/{uid}`, `/jobs/{uid}/note?count=100`, `/estimate?filter.job=`, `/invoice?filter.job=`; `/attachments/folders?filter.module=JOB&filter.module_uid=` only for photo rules.

### 4.2 Definitions
| Term | Counts | Does not count |
|---|---|---|
| **Attempt** | Any outbound call (any status/duration, incl. no-answer, voicemail, busy) or outbound text by a person | System/workflow texts (template wording: "welcome to Dream Team Roofing", "Reminder:", "on schedule to arrive", "Thank you for confirming") |
| **Talked** (reached) | A call either way with status completed/answered and **≥ 30 s** (CRM `TALK_SECONDS`), or a call with a summary showing a conversation; or the customer texted us (any non-empty inbound text) | Outbound "completed" < 30 s (likely voicemail) |
| **Confirmed** (visit) | Talked call in the confirmation window whose summary/transcript mentions the visit/time, or an inbound text after our reminder/ask with yes/ok/confirmed/see you/👍 (no "no/can't/reschedule/cancel"); or a talked call on the window day when no summary is available (say "call, no summary") | An attempt with no answer (that is an attempt, the visit stays **unconfirmed**) |
| **Updated** (customer) | Outbound text or talked call with content about the job | — |
| **Answered** (their text) | Any later outbound text or talked call | Our system text |
| **Happened** (visit) | IMAGE/VIDEO notes or photo answers that day; tech's call "heading/arrived"; report note; customer text about the visit | Stage move alone |

### 4.3 How to write the evidence (`report_issue.evidence`)
`<date ET> · <channel: Connect 7373 | Connect 0190 | Quo | Zuper note | Zuper history | proposal DTR-N | invoice> · <who: Zuper login / "office (Quo)" / customer> · "<exact words, ≤ 200 chars>"` — one line per fact, newest last, plus the **absence** you checked ("No outbound call/text 11-02 08:00 → 11-03 07:00 in Connect (2 conversations) or Quo"). Name sources that failed ("Quo returned 429 — not checked").

### 4.4 Avoiding false alarms
1. **Test job:** #701 (and #734) — test customer: audit, but prefix the title "TEST". Never mix their activity with real customers.
2. **Can't verify ≠ failure.** Empty SF checklists (all of them today), Frontdoor, in-person meetings, Zelle not recorded, Google reviews, job costing → list under "Can't verify", no alert, unless a chat proves the step was not done.
3. **Migrated jobs:** steps before 2026-10-31 live in the old-board history (`job_status[].category`), in old KPI fields and in chats. Look there before calling a step missing. Do not apply intake/inspection rules to a job whose history already shows that step done on the old board.
4. **Business time:** no deadline runs Sunday or 18:00–08:00. A Monday visit is confirmed on Saturday. Texting window 08:00–20:00 — no "should have texted" before 08:00.
5. **One person, two logins / one login, two people:** Zuper "Luis" may be Marienne; Quo is always Owen's user. Attribute to "office" unless the content names the person.
6. **Duplicates:** the same text in Connect and Quo within 2 min is one message.
7. **AI summaries can misname** people/companies ("Inti Roofing"). Quote the facts (dates, amounts, options), not names.
8. **Cancelled/declined/foreign-status jobs:** don't run stage rules; only SF-HYG-04 and open-money rules.
9. **Weather:** a rain postponement with the customer told is a worked H3, not a failure.
10. **REVISIT callbacks** start at Repair Day: skip intake/proposal rules; apply visit, quality and SF-QC-04.
11. **Data missing** (a 502, an empty Connect answer for a customer whose phone is missing) → "can't verify", never a failure.
12. **Severity cap:** nothing older than 7 days is `urgent` unless the customer is still waiting (CRM `MISSED_URGENT_DAYS`).

### 4.5 Days in stage for migrated jobs
`days_in_stage = now − job_status[-1].created_at`. If that entry and the one before it are both
SF entries made within 10 minutes on 2026-11-01..03 (the migration pattern `Got a job from AHS` →
stage), also compute `since_old = created_at` of the last old-board entry and report both
("Handoff Meeting 2 d; before that Proposal Made since 10-29"). Use `since_old` for stale rules
only when the old status meant the same step (e.g. Proposal Made → Proposal Creation).

---

## 5. Not detectable (say "can't verify")

| Step | Why | Best proxy |
|---|---|---|
| Any SF checklist step | All answers empty; none required; not linked to fields | Notes, chats, fields, records |
| Day-before / same-day confirmation as a *field* | No field or column; reminders OFF | Outbound call/text on the previous BD / visit day (Connect + Quo) — detectable that way |
| AHS claim submitted, AHS decision, AHS invoice, AHS payment | Frontdoor has no API | CRM "AHS approved" items (from AHS emails), notes/remarks, AHS lines on the job |
| Handoff meeting held, Antonio briefed, materials on hand, dry-in plan | In person / phone between staff, not logged | Staff texts (not in these APIs), notes |
| Customer sign-off, walkthrough, tile quality checks | SIGNATURE/answers empty | Customer chat after the repair |
| Zelle / check payments | Off-platform unless recorded | Proposal `deposit.status`, invoice `payment_history`, chat "payment sent" |
| Google review posted | Not in Zuper/Quo | — |
| Job costing, expenses, warranty sent, 6-month tag | Not set up / docs-only endpoints | Commission records only |
| Who (person) did it | Shared logins | "office" |
| Calls/texts on staff cell phones, CRM line (+1 954-775-8492), email | Not in Connect or Quo | — |

---

## 6. Open questions for Owen (defaults used above)

1. **Not answering cadence:** 1 attempt every BD, 2 (call + text) on day 1, escalate at 3 and 5 BD?
2. **AHS:** submit the claim within 1 BD of inspection; chase AHS at 3 BD; update the customer every 3 BD while waiting; chase AHS payment at 30 days (45 = today)?
3. **Proposal:** built ≤ 1 BD after authorization, sent ≤ 1 BD after built; follow-ups at 1/3/7 BD; deposit ≤ 2 BD after signing; never book a repair before signature (incl. $0 GOOD jobs?).
4. **Handoff:** repair booked ≤ 1 BD after signature + deposit (CRM "book" number reused); homeowner told the window ≤ 1 BD.
5. **Quality / review:** checklist says review link only if happy; the reviews agent decision (2026-10-22) offers it to everyone (anti-gating). Which wins?
6. **Callback:** REVISIT job ≤ 1 BD after a leak report, visit ≤ 3 BD?
7. **Money:** customer invoice ≤ 1 BD after repair; balance chase at 7 days; AHS lines on the job ≤ 2 BD; commission ≤ 7 days.
8. **Stale thresholds** per stage (§3.9) — especially Proposal Creation 5/10 BD and Quality Call 3 BD.
9. **Stage meaning:** the team keeps booked inspections in *Got a job from AHS* and booked repairs in *Handoff Meeting*/*Repair Day*; Day of Inspection is unused. Should stage lag be flagged at all (SF-HYG-01 is `watch`)?
10. **Closing:** where do cancelled/declined jobs go (no column)? And is *Job Completed* "paid", or can money still be open there?
11. **Checklists:** should the team answer them (then make key items required: intake 14, Day of Inspection 15, Proposal 7, Repair Day 6, Quality 2/4)? Today none is answered, so most process steps are only provable from chats.
12. **New work orders** still land on AHS - Inspection — switch `ahs_create` to Simple Flow / "Got a job from AHS"?
13. **Visit times at 06:30 ET** after the DST change (several 11:30Z visits) — real or a 1-hour shift?
