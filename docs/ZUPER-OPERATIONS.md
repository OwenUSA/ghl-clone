# Zuper operations — handoff (state as of 2026-09-25)

Everything a new session needs to manage Dream Team Roofing's **Zuper** account: the rules, how to
reach it, what is set up (the feature map), what runs on its own, and what is still open.
Nothing secret is in this file — only *where* things are. Never print a key, a password or a
customer's data.

The running log of every change (with backups and exact API bodies) is the memory note
`zuper-automation-off.md` in the Claude memory folder for this project. This file is the
organised summary.

---

## 1. The owner's rules (non-negotiable)

1. **Never send a customer anything automatically.** On 2026-09-17 a customer load triggered
   Zuper's template workflows and 231 customers got a mistaken email/SMS. Since then all 41
   workflows and all 21 customer-notification rules are **inactive**, and the owner re-enables
   automations himself, one at a time.
2. **Run the safety gate before every write:** `python3 /home/qa/zsafety.py` on dispatch
   (exit 0 = safe). It checks workflows, notification rules and the company-config switches.
   Every script and both containers below refuse to write when it fails.
3. **Never delete a job** — jobs carry photos. Move them (same job) instead. The only jobs ever
   deleted were test jobs created by us (#671 on 2026-09-22, #697 a minute after creating it).
4. **Back up before changing anything** (`/root/backups/` on dispatch), read back after, and
   compare. Test on the test job first (section 7).
5. **Zuper is where the team works now.** The CRM only *copies* Zuper (pull-only). An older
   Workiz export must never move a job backwards (the apply script enforces it, section 6).

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

### 3.1 Pipelines (Zuper "job categories"), in the order Zuper shows them

| # | Pipeline | uid | Notes |
|---|---|---|---|
| 1 | **AHS - Inspection** | `f07e3c01-97b1-404d-8929-175845774a60` | the old "AHS", renamed 2026-09-25; ends at **Approved** |
| 2 | **AHS - Repair & Review** | `2d0160ea-cf71-46f5-9911-e43d80c42d96` | new 2026-09-25 |
| 3–9 | Lead Qualification, Roof Inspection, Repair Service, Production - Roof / Metal Roof / Gutters / Siding | — | Zuper template pipelines, **not used** |
| 10 | **Retail** | `5939c976-002a-4d80-8bfd-2669a1f8843f` | |
| 11 | Miami Retail Repair | `520c342f-b1d6-4082-b047-2fd4039202c4` | Retail's 16 stages + checklists, no jobs yet |
| 12 | Miami Retail Roof Replacement | `bce5119a-58cd-46d9-8fa9-cb420eb8c940` | 〃 |
| 13 | Miami Gutters | `a29e16c7-590b-47ac-9527-162b71cdc488` | 〃 |
| 14 | Sarasota Repairs | `6af83eaa-50f0-426b-93ad-ee46afcd0b43` | 〃 |
| 15 | Sarasota Roof Replacement | `601fd5ee-5040-4d7f-8a9a-459e62e1a0e0` | 〃 |
| 16 | Sarasota Gutters | `fda33634-1655-45fb-8503-d5d3111745d6` | 〃 |
| 17 | Leak repair | — | made by someone on the team (not by us) |

**Stages**

- **AHS - Inspection:** Work Order Received → Contact Attempted → Scheduled → Inspection →
  Inspection Completed → Notify Auth Dept → Proposal Made → Approval Requested → **Approved** ·
  side: Call Back, Waiting for Customer, Reschedule Required, Estimate Declined, Cancelled.
  ("Inspecting" was renamed "Inspection" — same status uid; jobs moved before the rename still
  *display* "Inspecting" on their page until they next move. "On My Way" was deleted.)
- **AHS - Repair & Review:** Repair Scheduled → Repair In Process → Repair Complete → Invoice
  Submitted to AHS → Awaiting AHS Payment → Paid → Review Requested → Review Received · side:
  Call Back, Waiting for Customer, Reschedule Required, Cancelled.
- **Retail** (and the 6 regional copies): New Lead, Contact Attempted, Inspection / Estimate,
  Proposal Made, Estimate Sent, Follow Up, Scheduled, Repair In Process, Repair Complete, Invoice,
  Collect Balance, Paid, Waiting for Customer, Reschedule Required, Estimate Declined, Cancelled.

Job counts on 2026-09-25: AHS - Inspection 43 (plus the test job), AHS - Repair & Review 290
(221 of them Paid), 427 jobs in the account.

### 3.2 Hand-off Inspection → Repair (automatic)

Zuper has no native pipeline link. The **`zuper_tasks` container** (section 4) checks every
2 minutes: an AHS - Inspection job at **Approved** is moved — the same job, with its history,
answers, fields, photos and tasks — to **AHS - Repair & Review → Repair Scheduled**, and verified.
By hand: edit the job's Category in Zuper and pick the stage.

### 3.3 Stage checklists (the forms that pop up on a stage move)

| Board / stage | Questions | Notes |
|---|---|---|
| AHS - Inspection / Contact Attempted | 18 (intake) | 17 copy to job fields ("Checklist" group); "Intake complete?" is required, a "No" blocks the move |
| AHS - Inspection / **Inspection Completed** | 12 (9 written + 3 photo sets) | moved here from "Inspection" 2026-09-25 (owner: pop up after the inspection); the 9 written ones copy to job fields |
| AHS - Repair & Review / Repair In Process | 1 (Photos - before) | |
| AHS - Repair & Review / Repair Complete | 2 (Photos - during / after) | |
| Retail / Contact Attempted | 15 (intake, no AHS-only questions) | copy to job fields |
| Retail / Inspection / Estimate | 12 | 9 written copy to job fields |
| Retail / Repair In Process, Repair Complete | 1, 2 photo questions | |
| 6 regional boards | same as Retail | **inspection questions NOT linked to job fields yet** |

Deleting a checklist question keeps the answers already saved on jobs (tested). Answers live in
each job's status history (`job_status[].checklist`), and linked ones also in job fields.

### 3.4 Job fields (custom fields)

- 40 JOB fields. Groups: **Checklist** (17 intake + 9 inspection, shown on AHS - Inspection,
  AHS - Repair & Review and Retail), **AHS** (Authorization Amount, Customer Responsibility,
  Estimate Amount), **CRM** (Callback Status…). Workiz Job #, Job Type, Technicians, Technician
  are ungrouped on every job — that is how the original load created them.
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
customer notification rules and workflows inactive · `jobs.public_link = false` · invoice public /
payment links off · `jobs.notify_user_on_assignment` on (staff only; `assign_sync.py` switches it
off while it runs) · vendor auto-emails on purchase/service orders are on (vendors, not customers).

### 3.9 Routes and technicians

Dispatch cron `/etc/cron.d/zuper-routes` → `/home/qa/zbrowser/routes_sync.sh` every 10 min: one
route per Technician per day ("<Tech> - <Day>"), future days only (Zuper refuses to change a route
that has departed). `assign_sync.py` (manual, dry run by default, `--apply`) makes each live job's
assigned user match its Technician dropdown. Technicians: Owen, Antonio (own login since 09-22).

---

## 4. What runs on its own

| Where | What | Every | Code / logs |
|---|---|---|---|
| owen-main | container **`zuper_proposals`** — side-by-side + full proposal PDFs for DRAFT proposals | 60 s | `/opt/santiagoproperties/zuper-proposals/` (`app/`, `data/compare_sync.log`, `data/state.json`) |
| owen-main | container **`zuper_tasks`** — the 4 checklist tasks + the Approved hand-off | 120 s | `/opt/santiagoproperties/zuper-tasks/` (`app/tasks_sync.py`, `data/tasks_sync.log`, `data/tasks_state.json`); env `TASKS_EXTRA_JOBS` keeps test job #694 in scope |
| owen-main | CRM (`ghl_clone_api`/worker) — one-way mirror of Zuper by webhook + 15-min sweep | — | `ZUPER_SYNC_ENABLED=true`, `ZUPER_PULL_ONLY=true` |
| dispatch | cron `zuper-routes` | 10 min | `/home/qa/zbrowser/routes_sync.sh` / `routes_sync.log` |
| dispatch | ~~cron zuper-compare~~ | — | moved to owen-main 2026-09-24; old file in `/root/backups/cron.d-zuper-compare.disabled-20260924` |

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
  (santiagovillahermosa@gmail.com, +15618999051, "TEST - 100 Test St, Bradenton").
- Job **#694** `6a38bac0-25fc-44ce-88df-2cf516da06a2` — now in **AHS - Inspection / Work Order
  Received** for the owner's testing; has the 4 tasks, test proposals DTR-11…14, invoice #1.
- Moving #694 between boards is safe and was tested repeatedly (nothing lost).

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
| Job fields | `PUT /jobs {"job":{"job_uid","custom_fields":[{label,value,group_name,group_uid,type}]}}` |
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

## 9. CRM side (repo `OwenUSA/ghl-clone`)

- The CRM mirrors Zuper one way. **Branch `feature/zuper-new-pipelines` is NOT deployed.** It
  adds: the 6 regional boards (`mapping.MIRROR_ONLY_CATEGORIES`), "AHS - Repair & Review",
  `CATEGORIES` AHS → "AHS - Inspection", `mirror.ALIASES` "Inspecting" → "Inspection",
  `mirror.MERGES` "On My Way" → "Inspection", a regional card is never sent to Zuper, docs in
  DECISIONS.md. 278 Zuper tests pass (run them on dispatch — the Node-driven tests fail on
  Windows paths).
- Until it is deployed, the CRM still shows the old AHS pipeline and the 290 moved jobs' cards
  stay where they were (the engine rejects an unknown stage — nothing breaks).
- **Deploy (needs the owner's go-ahead):** merge the branch to `main` (fast-forward) and push; on
  owen-main `cd /opt/santiagoproperties/ghl-clone && ./deploy.sh` (no migration); then in the api
  container `python -m app.zuper.mirror --phase boards --phase backfill` **dry run**, show the
  owner, then `--commit` with **boards and backfill together** (so no card sits on a wrong stage).
- Worktrees: local `../ghl-wt-pipelines` (the branch); dispatch `/home/qa/wt/zuper-pipelines`.
  **The main checkout `ghl-clone/` is shared with another session** (voice agents) — do not switch
  its branch.

---

## 10. Open items

- [ ] Deploy the CRM branch (section 9) — owner's go-ahead.
- [ ] Checklist tasks for Retail + the 6 regional boards (owner said AHS first); link the regional
      boards' inspection questions to job fields first.
- [ ] Invoice payment terms (still Zuper's sample text); proposal email wording.
- [ ] Contract image page 1 (old phone/email) and page 14 "INITIALS" line — needs a new contract PDF.
- [ ] Optionally add a job field for "Intake complete?" or make it optional (so intake can be
      finished without the popup).
- [ ] Optionally move Retail + regional pipelines up the list and the unused template pipelines down.
- [ ] Old test proposals DTR-11…14 and invoice #1 on #694 can be deleted when testing is done
      (they are the test customer's).

---

## 11. Backups (dispatch `/root/backups/`)

Key ones from 2026-09-24/25: `ahs_jobs_full_before_split_20260925.json` (all 333 AHS jobs, full),
`ahs_statuses_*`, `ahs_checklists_20260925.json`, `ahs_inspection_checklist_before_move_20260925.json`,
`job_custom_fields_before_insp_20260925.json`, `job_field_groups_20260925.json`,
`job_categories_before_reorder_20260925.json`, `zuper_company_config_before_*`,
`zuper_company_profile_before_*`, `dtr_layout_before_*`, `dtr_products_before_html_20260924.json`,
`duplicate_inspection_completed_tasks_20260925.json`, `export19_before_apply.json`,
`job694_*`. Scripts keep `*.bak-<date>` copies next to themselves.
