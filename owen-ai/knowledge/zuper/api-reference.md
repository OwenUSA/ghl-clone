# Zuper API reference for Owen AI

Built 2026-11-02 from the repo (`backend/app/zuper/*`, `dispatch/*`, `autoreply/*`, `docs/ZUPER-*`,
`DECISIONS.md`), the project's Claude memory notes (live-tested facts, 2026-09-17 → 2026-10-27), and
the official docs at developers.zuper.co (OpenAPI pages, fetched 2026-11-02). **No live call was
made to write this.** The live account's current state is in `live-account.md` (separate file).

## Markers

| Marker | Meaning |
|---|---|
| **V** | Verified against THIS live account (date). Wins over the docs. |
| **C** | In repo code, never confirmed live (the code itself says UNVERIFIED). Treat as a guess. |
| **D** | Official docs only. Never exercised on this account. |
| **X** | Tried live and failed / behaves differently from the docs. Do not use as documented. |
| **!** | Customer-facing or destructive. Read `safety-rules.md` before even considering it. |

Everything not GET is a write and needs the owner's explicit, per-change OK (`safety-rules.md`).

---

## 0. Basics

| Item | Value | Mk |
|---|---|---|
| Base URL | `https://us-east-1.zuperpro.com/api` | V |
| Region lookup | `POST https://accounts.zuperpro.com/api/config {"company_name"}` → `config.dc_api_url`, `dc_name` (no key sent) | D/C |
| Auth | header `x-api-key: <key>`; HTTPS only. Key file locations: `ZUPER-OPERATIONS.md` §2 — never print a key | V |
| Key identity | The production key belongs to **Owen Buzaglo's user**: every API write shows in Zuper as "by Owen", activity rows carry `metadata.request_source.type = "API_KEY"`. Never tell anyone "Owen did it" for an API_KEY line | V |
| Read-only keys | Developer Hub → API Keys → "Read-only" box restricts a key to GET. A key inherits all permissions of its user. Recommended for any AI agent | D (memory 2026-10-27 recommends) |
| Other hosts (same key) | Workflows `https://us-east-1-workflow.zuperpro.com/api`; Zuper Connect `https://us-east-1-connect.zuperpro.com/api/telephony`; webhooks `https://us-east-1.zuperpro.com/service/notifications/webhook` (**no `/api`**) | V |
| MCP server | `https://mcp.zuperpro.com/mcp` (streamable HTTP), headers `x-api-key` + `x-account-region: us-east-1`. 290 tools incl. `send_estimate`, `send_invoice`, `send_job_card` etc. — exposes SENDS; never connect it with a write-capable key | D |
| Envelope | Success `{"type":"success","data":…}`; lists add `total_records`, `total_pages` (sometimes `current_page`). Error: HTTP 4xx and/or `{"type":"error","message"\|"title"}` — **some 200 answers carry `type:"error"`** (docs' own PUT /jobs example even says `type:"error", message:"…updated successfully"`) → check `type` AND read back | V/D |
| Create answers | uid location varies: top level, `data.<x>_uid`, nested one level, or **absent** (status create, job create sometimes) → re-list by name to find it (`client.uid_of`) | V |
| Dates | Write times as `"YYYY-MM-DD HH:mm:ss"` **UTC** (`dispatch/writes.ZUPER_TIME`); reads return ISO 8601 UTC. Company timezone America/New_York | V |
| Money | number or string of dollars; repo converts to cents (`client.cents`) | V |
| Rate limit | Docs: 200–700 req/min by plan; headers `X-Rate-Limit`, `X-Rate-Limit-Remaining`; 429 + `Retry-After` (s). Repo paces at **180/min** (`ZUPER_REQUESTS_PER_MINUTE`) across threads, retries 429 ×6 honouring Retry-After (cap 60 s), retries GET 5xx ×2. Occasional **502s** are normal (retry next loop) | D/V |
| Pagination (GET lists) | `page` (1-based) + `count` (repo uses 100); stop when `page >= total_pages` or a short page. **Exceptions:** `/attachments` returns 10/page whatever `count`; `/invoice_estimate/proposal_template` with large `count` returns only 3 → use `count=10`; `/activities/recent` use `count=50` | V |
| Pagination (filter lists) | `POST /<module>/filter {page, limit, sort, sort_by, filter_rules:[{key,operator,value,field_type,module,type}], filter_rule_operator, cursor, cursor_pagination}`; field keys from `GET /<module>/meta/filter`. What the web app uses | D/V(web JS) |
| Undocumented endpoints | Found by grepping web-app JS bundles (`web.zuperpro.com` → `main-*.js` → chunks), then tested on a test record. Marked "(web JS)" | V |

### How the repo talks to Zuper (`backend/app/zuper/client.py`)
`request()` order, before any connection: (1) **DENYLIST** (regex on path, any method, see §14),
(2) **ALLOWLIST** (exact method+path), (3) sync switch/key, (4) dry-run scope refuses non-GET,
narrow write scopes (`ahs_create`, `dispatch_write`, `proposal_lines`, `autoreply_write`,
`followup_write`, `payments_write`, `reviews_write`, `dropped_call_write`) each allow only their
own 1–4 writes under their own server gate, (5) `ZUPER_PULL_ONLY` backstop refuses every other
non-GET, (6) pacing/back-off. Host must be https under `zuperpro.com`. Connect reads go only
through `connect_read` (5 patterns, §11). Owen AI should copy this pattern: deny-first, allow-list,
read-back after every write, never retry a write with an unclear outcome.

---

## 1. Jobs

| Method & path | Purpose | Key params / body | Response essentials | Mk / gotchas |
|---|---|---|---|---|
| `GET /jobs` | list jobs | `page,count,sort,sort_by`; filters `filter.keyword`, `filter.category`, `filter.job_status`, `filter.updated_at_from/_to`, `filter.created_at_from/_to`, `filter.from_date/to_date` (schedule), `filter.work_order_number`, `filter.customer`, `filter.assigned_to`, `filter.is_deleted`, `filter.parent_job`, `filter.job_type`, `filter.status_in_history`, `filter.delayed_job`, `filter.is_invoiced` … | `data[]` rows: `job_uid`, `work_order_number` (= the **job #**), `job_title`, `job_category{category_uid,category_name}`, `current_job_status{status_uid,status_name,status_type}`, `job_status[]` (moves; list rows lack `done_by`), `customer{customer_uid,customer_first_name,customer_last_name,customer_company_name,customer_contact_no{mobile,…}}`, `customer_address{street,city,state,zip_code,geo_cordinates[lat,lng]}`, `scheduled_start_time/_end_time`, `assigned_to[{user{…},assigned_at}]`, `created_at`, `updated_at`, `is_deleted`, `job_total` | V (list + `filter.keyword`); other filters D. `filter.updated_at_from` with a future moment must return 0 — the sweep checks this |
| `POST /jobs/filter` | rule-engine list (web app) | see §0 filter lists; `fields_to_select`, `cursor_pagination` | same rows | D / V(web JS) |
| `GET /jobs/{job_uid}` | whole job | — | adds `custom_fields[{label,value,group_name,group_uid,type,hide_to_fe,hide_field,read_only,_id}]`, `products[]` (line items: `product_id` code, `product_uid`, `product_name`, `quantity`, `price`, `total`, `tax`…), `job_status[]` with `status_history_uid`, `created_at`, `done_by`, `time_on_status` (minutes), `checklist[{question,answer,type}]` (photo answers = URL lists), `job_type` NEW/REVISIT, `parent_job`, `child_jobs` | V. Job record `attachments`/`assets`/`cover_image` are always empty (photos live on notes) |
| `POST /jobs` | create job | `{"job":{job_title, job_category:<uid>, customer_uid:<uid>, due_date, job_priority, customer_address{street,city,state,zip_code,country}, custom_fields[], assigned_to[{user_uid}], [parent_job, job_type]}}` | uid often NOT where expected — find by title+category | **V with gotchas (2026-09-28):** write key is **`customer_uid`**, NOT `customer` (→ "Either customer or organization data is required"); **`due_date` mandatory** ("End / Due Date is Mandatory"); `due_date` alone = unscheduled job (`is_scheduled:false`). No status named → lands in the board's `NEW`-type column; a board with no NEW column strands the job in no column. Docs show a nested `customer{}` object → **docs wrong for this account** |
| `PUT /jobs` | update job | `{"job":{"job_uid", …only what changes…}}`; query `update_all_jobs`, `clear_schedule`, `remove_parent_job` (D) | `{type,message}` | **V, dangerous:** `custom_fields` sent here **REPLACES the whole field list** (2026-10-06: 1 sent, 33 → 1); `products` sent here **REPLACES all line items** — always GET, merge, send full list; `job_total` is stored, not recomputed — send it explicitly if it must change; **scheduled times are silently IGNORED** (200, no change). Move board: `{"job":{job_uid, job_category:<uid>}}` keeps history/fields/photos/tasks, then set status. Revisit: `{job_uid, job_type:"REVISIT", parent_job:<uid>}` (REVISIT without parent → refused). Job address `{job_uid, customer_address{…}}` = **C (unverified)** |
| `PATCH /custom_fields` | update custom fields — **MERGE** | `{"module_name":"JOB","module_uid":<job_uid>,"custom_fields":[{label, value, group_uid, [group_name, hide_to_fe, hide_field, read_only]}]}` | `{type:"SUCCESS", message:"Updated N custom fields…"}` | **V 2026-10-27** (cleared 36 fields on test job #701) + D. Only sent fields change; matched by `label`+`group_uid`; `""` clears; `null` rejected; unknown labels are still SAVED (ungrouped) — spell labels exactly. **Preferred over PUT /jobs for fields.** Docs: also CUSTOMER, ESTIMATE, INVOICE, PROPERTY, PRODUCT… |
| `PUT /jobs/{uid}/update` | schedule / unassign (web JS, undocumented) | reschedule `{"job":[{"scheduled_start_time":"YYYY-MM-DD HH:mm:ss","scheduled_end_time":"…","type":"SCHEDULE"}]}`; unassign `{"job":[{"user_uid","team_uid","type":"UNASSIGN"}]}` (`team_uid` required; also unassigns that user's tasks on the job) | 200 | **V**. A reschedule fires Zuper's "Schedule Job" workflow trigger (customer reminders when those workflows are active) |
| `PUT /jobs/schedule` | reschedule (docs) | `{job_uid, from_date, to_date, remove_from_route, reason, work_mins_required, job_timezone, appointment_uid}` | — | D (repo uses `/jobs/{uid}/update`) |
| `POST /jobs/assign` | assign / unassign users or teams | `{job_uid, type:"ASSIGN"\|"UNASSIGN", users[], teams[], notify_users:false, update_all_jobs}` | — | D. Company `jobs.notify_user_on_assignment` is ON (staff push) |
| `PUT /jobs/{uid}/status` | move to a column (status) | simple `{"status_uid","remarks"}`; with answers `{job_uid,status_uid,status_name,customer_signature:"",remarks,checklist:[{question,answer,type}]}`. Docs add `feedback{rating}`, `geo_cordinates`, `eta` | `{}`/success | **V.** Answers must be **strings** (a list → 500). Required checklist questions (incl. required photo/video) are **NOT enforced by the API** → an API move silently skips the stage's questions (intake, inspection, KPI) — repo refuses to API-move into any column that has a checklist. Works out of a CANCELED column (V 2026-10-27). A move fires column workflows. Never into a `CLOSED`-type column (then Zuper refuses all further status changes) |
| `PUT /jobs/{uid}/status/rollback` | go back / delete a status entry | `{job_uid,status_uid,status_index,reason,user_uid}` (D) | — | C/D. Repo tries 3 body shapes only while refused |
| `PUT /jobs/{uid}/status/{status_uid}/checklist` | edit checklist answers of a history entry | query `status_name,index,checklist,remarks` | — | D, unused |
| `DELETE /jobs/{uid}/delete` | delete job | — | — | V. **FORBIDDEN** for Owen AI (photos) |
| `POST /jobs/{uid}/recover` (code) vs `POST /jobs/recover` (docs) | restore | — | — | C vs D — conflict, untested |
| `GET /activities/recent?count=50&page=N` | account activity log, newest first | — | rows: `users`, `activity_action_uid` (= job uid for jobs), `activity_message`, `metadata.request_source` (a **Python-repr string** of a dict; `type` USER/API_KEY/MOBILE…) | V |
| `GET /activities/job?job_uid=&page=&count=` | one job's activity | `filter.from_date/to_date`, `filter.user_uid`, `filter.activity_action` | `data[]` + `total_pages` | D |
| `GET /jobs/meta`, `/jobs/meta/filter` | list fields / filter operators | — | — | V (read 2026-10-15) |
| `GET /jobs/{uid}/finance/stats` | job financial summary | — | — | D |
| `POST /jobs/{uid}/card` | job card PDF / **email it** | — | — | D, **!** (can email customer) |

### Job categories (boards) and statuses (columns)

| Method & path | Purpose | Body | Mk / gotchas |
|---|---|---|---|
| `GET /jobs/category` | boards | `?populate_statuses=true` includes columns | V; rows `category_uid`, `category_name`, `job_statuses[]` |
| `POST /jobs/category` | create board | `{"category":{"category_name"}}` | V 2026-09-23. **Conflict:** DECISIONS 2026-09-17 says the flat body was accepted, memory 2026-09-23 says flat refused and wrapped worked; `{"job_category":{…}}` → "Category Name Missing". Use `category` wrapper |
| `PUT /jobs/category` | rename | `{"category":{category_uid,category_name}}` | V |
| `PUT /jobs/category/reorder` | order (web JS + D) | `{"categories":[{category_uid,display_order}]}` | V |
| `GET /jobs/status/{category_uid}` | columns of a board | — | V: `{data:{_id, job_statuses:[{status_uid,status_name,status_type,status_color,checklist…}]}}`. (On 2026-09-17 it once returned empty right after a create — re-list before trusting absence) |
| `POST /jobs/status_new/{category_uid}` | add column | `{"job_status":{status_name,status_type,status_color}}` | **V 2026-10-27 (wrapped body works, answers with NO uid — re-list).** Flat body answered OK and created nothing (2026-09-17). Repo `zapi.status_create_candidates` still tries flat first — wrong order |
| `PUT /jobs/status/{category_uid}` | edit column (web JS + D) | `{"job_status":<FULL record incl. status_uid>}` | V. Repo `PATHS.status_update` = `PUT /jobs/status/{cat}/{status}` with a partial body — **C, likely wrong** |
| `PUT /jobs/status/{cat}/rearrange` | reorder | `{"job_status":[all uids in order]}` | V. Side effect: Zuper fills `internal_key` on every column |
| `DELETE /jobs/status/{cat}/{status}` | delete column | — | V; jobs keep it in history and keep showing the old name until moved |
| `POST /jobs/status/{cat}` | **bulk import** | — | **NEVER** (replaces columns) |

Status types: `NEW, LEAD, ESTIMATING, SOLD, LOST, PRODUCTION, INVOICING, SCHEDULED, ON_MY_WAY, STARTED, COMPLETED, ON_HOLD, FOLLOW_UP, PAID, CANCELED, OTHER, CLOSED`. Never use `CLOSED`. A job keeps the type it entered with (`current_job_status.status_type`).

### Stage checklists (the forms that pop up on a column move)

| Method & path | Body | Mk |
|---|---|---|
| `GET /settings/checklist?category_uid=&job_status_uid=` | — | V (answer key is `is_required`, not `required`) |
| `POST /settings/checklist/new` | `{"prefill_checklist":false,"job_status_uid","category_uid","checklist":{label,field_type,component,required,options,meta_options.restrict_status_update{is_enabled,restricted_options,message}}}` — appends one | V |
| `PUT /settings/checklist/{uid}` | edit one (incl. link: copy_to_field, prefill_value) | V |
| `DELETE /settings/checklist/{uid}` | answers already on jobs are KEPT | V |
| `PUT /settings/checklist/reorder`, `POST /settings/checklist/clone`, `PATCH /settings/checklist/bulk-settings` | — | D |

Field types: short/long text (`SINGLE_LINE`/`MULTI_LINE`), date/time, `RADIO` (single), `MULTI_ITEM`
(checkbox), `SINGLE_ITEM` (dropdown, `options:[plain strings]`), `FILE`, `IMAGE`, `MULTI_IMAGE`,
`VIDEO`, `MULTI_MEDIA`, barcode, lookup, table, signature, header. **No audio and no number type
seen** — money questions are short text (digits only).

### Job custom-field DEFINITIONS (settings, not values)

| Method & path | Mk / gotcha |
|---|---|
| `GET /settings/custom_fields?module_name=JOB` | V (web app). Repo sends `?module=` — C, may be ignored |
| `POST /settings/custom_fields/new {"module_name":"JOB","custom_field":{…}}` | V — adds ONE |
| `PUT /settings/custom_fields/{uid}` | V |
| `POST /settings/custom_fields` | **NEVER** — full-list REPLACE; wiped 68 fields on 2026-09-17 |
| `GET /settings/custom_fields/group?filter.module_name=JOB`; `POST /settings/custom_fields/group {"custom_field_group":{group_name,module_name,associated_to:[cat uids],category,group_description,order_no}}`; `PUT /settings/custom_fields/group/{uid}` | V |

---

## 2. Notes and photos

| Method & path | Purpose | Body / params | Response | Mk / gotchas |
|---|---|---|---|---|
| `GET /jobs/{uid}/note` | job notes | `count=100`, `sort`, `sort_by` | rows `note_uid`, `note` (**HTML**), `note_type` TEXT/IMAGE/VIDEO/AUDIO/DOCUMENT, `attachments[{attachment (URL), attachment_uid, mime_type, attachment_visibility}]`, `is_private`, `created_by`, `created_at`, `is_deleted` | **V. A job's photos ARE its IMAGE/VIDEO notes' attachments.** Strip HTML (`dispatch/reader.note_text`) |
| `POST /jobs/{uid}/note` | add note | repo (V): `{"note":{"note":<text>,"is_private":true,"notify_users":false}}`. Docs: `notify_users` is a **query** param; body `note{note_type,note,attachment(s),user_mentions}` | `{data:{note_uid}}` | **V** (autoreply/dispatch/AHS paths). Always `is_private:true`; send `?notify_users=false` too (doc vs code disagree on where it goes). Read back by text |
| `PUT /jobs/{uid}/note/{note_uid}/update` | edit note | `?notify_users` | — | D. **Repo's `update_note` uses `PUT /jobs/{uid}/note/{note_uid}` which docs say is "Change Note Privacy" (needs `?is_private=`) — repo path likely wrong** |
| `DELETE /jobs/{uid}/note/{note_uid}` | delete | — | — | allow-listed in repo; **forbidden** for Owen AI (photo notes) |
| `GET /notes?filter.job=&filter.customer=` | notes account-wide | — | — | D (web app: `/notes?filter.job`) |
| `GET /attachments/folders?filter.module=JOB&filter.module_uid=<job_uid>` | job albums + counts | — | rows `folder_name`, `attachment_count` | V 2026-10-22. Default albums are copied onto a job only at creation |
| `GET /attachments/group` | gallery | many filters | — | D/V(web JS). **Never modify/delete gallery photos** |
| `GET /attachments` | account-wide | — | 10/page, no owner field, `filter.module_uid` matches nothing | X for per-job use |
| `GET /jobs/{uid}/attachments` | — | — | — | **X: 404** (docs list POST/PUT/DELETE variants; do not use) |
| `POST /misc/upload` (multipart `attachment`, `attachment_type=ATTACHMENT`) | upload a file | — | attachment record | V (proposal PDFs). Then `POST /note_attachment {module_name:"ESTIMATE",…}` to attach to a proposal (V) |

---

## 3. Service tasks

| Method & path | Body | Mk / gotchas |
|---|---|---|
| `GET /service_tasks?filter.module=JOB&filter.module_uid=<job_uid>` | — | **V.** Repo `PATHS.job_service_tasks` = `/jobs/{uid}/service_tasks` → **X 404** (the CRM mirror's task pull is broken) |
| `POST /service_tasks` | `{"execution_type":"PARALLEL","is_enabled":true,"service_tasks":[{module:"JOB",module_uid,service_task_title,service_task_description,task_type:"TASK",[priority,assigned_to:[{user_uid,team_uid}],assign_users_to_module:false]}]}`; `?notify_users=true` rings the assignee's bell | V. Answer `data.service_task_uids[]`. **Gotcha:** company `jobs.auto_add_task_assignee=true` ignores `assign_users_to_module:false` and ADDS the assignee to the JOB (changes routes for technicians) |
| `PUT /service_tasks/{uid}` | `{"service_task":{…}}` | V |
| `PUT /service_tasks/{uid}/status` | `{service_task_status: OPEN\|IN_PROGRESS\|ON_HOLD\|COMPLETED\|CANCELED\|INCOMPLETE, remarks}` | V |
| `PUT /service_tasks/{uid}/assign` | — | D |
| `DELETE /service_tasks/{uid}` | — | V (forbidden without OK) |

The `zuper_tasks` container owns the 4 tasks on every live AHS job (`operations.md` §6) — never edit or delete those; a task whose status a person changed is left alone by it.

---

## 4. Customers

| Method & path | Purpose | Body | Mk / gotchas |
|---|---|---|---|
| `GET /customers` | list | `filter.keyword` (matches phone digits, name…), `page,count`, `filter.is_deleted`, `filter.updated_at_from` | V. Find a customer by phone: `filter.keyword=<10 digits>` (V, used by workflows) |
| `GET /customers/search?q=` | search | — | D |
| `GET /customers/{uid}` | one | — | V: `customer_uid`, `customer_first_name/last_name`, `customer_company_name`, `customer_contact_no{mobile,home,work,phone}`, `customer_address{…}`, `customer_tags`, `custom_fields`, `is_deleted` |
| `GET /customers/{uid}/summary?modules=` | jobs/quotes/invoices summary | — | D/V(web JS) |
| `POST /customers_new` | create | `{"customer":{…}}` | V (creates no job). "Customer Already Exists" refusal seen live |
| `PUT /customers/{uid}` | update | `{"customer":{…}}` | C. Forbidden without OK |
| `DELETE /customers/{uid}` | delete | — | allow-listed in repo; **forbidden** |
| `POST /customers/{uid}/send` | **SEND SMS/EMAIL** | `{"type":"SMS","sms":{"body","to":"+1XXXXXXXXXX"}}` | **V, !!** Sends from the company **System Notification Number** (must be 19544207373; check `GET /company/config` → `data.zuper_connect.system_notification_number` + `_enabled` first). Docs: `sms.from` exists, fire-and-forget (200 ≠ delivered), 400 if customer opted out; memory says no from-number param — untested. Denylisted in the repo except inside named scopes. Owen AI: NEVER without approval of the exact text |
| `POST /customers/{uid}/portal/invitation`, `/send_add_card_email` | — | — | **!! never** |
| `GET /customer_notification`, `PUT /customer_notification/{uid}/activation?is_active=` | customer notification rules (21, all inactive) | — | V. Activating one = automatic customer messages → **never** |

`GET /property`, `/property/{uid}` (D): properties exist (422 in backup) but the repo does not use them.

---

## 5. Users, teams, technicians

| Method & path | Mk / notes |
|---|---|
| `GET /user` | V — the key's own user (Owen) |
| `GET /user/all` | V — rows `user_uid`, `first_name`, `last_name`, `email`, `role`, `designation`, `is_active` |
| `GET /user/company` | V — company profile (`PUT /company {"company":{…}}` to edit; `PUT /user/company` → 404) |
| `GET /teams/summary`, `GET /team/{uid}` | D/V(web JS). Team add `POST /team/assign {team_uid,user_uid}` (V) |
| `POST /user` | V (created Antonio's login 2026-09-22) — never without OK |
| `GET /access_role`, `PUT /access_role/{uid} {"access_role":{permission_uids:[FULL list],role_name,role_description}}` | V 2026-10-27 — full-list replace |
| Technician on a job | the **`Technician` job custom field (dropdown)** drives routes and assignment; `Technicians` (text) is Workiz's legacy value; `assigned_to[]` is Zuper's assignment |

---

## 6. Appointments, schedule, routes

| Method & path | Mk / notes |
|---|---|
| `GET /appointments?filter.job_uid=` | **X: 403 — the appointments module is off on this account.** Visits are the JOB's `scheduled_start_time/_end_time` |
| `POST/PUT/DELETE /appointments…` | D, unusable here |
| `GET /jobs/schedule?from_date,to_date,view`; `POST /jobs/dispatch_board/filter`; `POST /jobs/employee/schedule/filter` | V(web JS) reads |
| `GET /routes?from_date,to_date`; `POST /routes`; `PUT /routes/{uid}`; `POST /routes/{uid}/assign\|unassign {users:[{user_uid,team_uid}]}` | V. Departure must be in the future (`400 Departure in past`); PUT ignores users. **Routes are owned by the `zuper-routes` cron — never touch a route named "<Tech> - <Day>"** |
| `GET /assisted_scheduling`, `PUT /jobs/schedule/overlap` | D |

---

## 7. Products / catalog (parts & services)

| Method & path | Body | Mk |
|---|---|---|
| `GET /product?page=&count=` (`filter.keyword`, `filter.product_category`), `GET /product/{uid}` | — | V. Fields `product_uid`, `product_id` (**the code**, e.g. `AHS-LEAK-REPAIR`), `product_name`, `product_description` (HTML), `product_type` SERVICE/PART, `price`, `purchase_price`, `uom`, `product_category` |
| `POST /product {"product":{…,"product_category":<uid string>}}` | create | V |
| `PUT /product/{uid} {"product":{…}}` | GET full, drop `created_by/created_at/updated_at/product_no/is_deleted`, send | V. Keep descriptions HTML (`<p>`,`<ul><li>`); UI edits flatten them. Product edits affect only NEW proposals |
| `GET /products/category`; `POST /products/category {"product_category":{category_name,category_description}}` | — | V. Categories: **AHS** (`e69bb351-…`), **DTR Packages** |

Codes and what reports do with them: `operations.md` §7.

---

## 8. Proposals / quotes (`/estimate`)

A proposal is an `estimate` with `is_proposal:true` and `proposal_options[]` (Good/Better/Best).

| Method & path | Purpose | Body / params | Response essentials | Mk / gotchas |
|---|---|---|---|---|
| `GET /estimate` | list | `filter.job`, `filter.customer`, `filter.is_proposal`, `filter.is_converted`, `filter.is_deleted`, `page,count` | rows `estimate_uid`, `estimate_no`, `estimate_status`, `proposal_options[{is_accepted}]`, `job{job_uid,work_order_number}` | V |
| `GET /estimate/{uid}` | one — **the prices quoted** | — | `estimate_no`, `estimate_status` (DRAFT/AWAIT_RESPONSE/APPROVED/DECLINED/…), `job{job_uid,work_order_number}`, `customer`, `proposal_title`, `proposal_options[{option_uid, option_name, is_accepted, total, line_items[{product_uid, product_id, name, description, quantity, unit_price/price, total, product_type, uom}]}]`, `is_deleted` | V. Accepted option = `is_accepted:true`. Use line `total` (report rule) |
| `POST /estimate` | create proposal (DRAFT) | docs: `{"estimate":{proposal_title*, proposal_template*:<template uid>, job:<job_uid>, customer:<uid>, property, proposal_options[], estimate_date, expiry_date, remarks, tags}}` | `data.estimate_uid` | **D for the body** (test proposals were made by dispatch scripts `test_proposal9/10/11.py`; template for a new one). Create FROM the job (`job` set) or it is never copied to the job / reports. Use a DTR template. Within ~1–2 min the `zuper_proposals` container attaches `Proposal-DTR-N.pdf` to every DRAFT — expected, do not remove |
| `PUT /estimate/{uid}` | edit options/items | `{"estimate":{estimate_uid, is_proposal:true, proposal_title:<current>, proposal_options:[…FULL…]}}` | — | **V.** Without `proposal_title` → 400; without `is_proposal` → 200 but IGNORED; flips option `financing.is_enabled` to false. DRAFT only — never edit a sent/accepted proposal |
| `PATCH /estimate/{uid}/options/{option_uid}` | hide / recommend option | `{is_hidden,is_recommended}` | — | D; **X for line items** (200, ignored) |
| `POST /estimate/{uid}/send?send_proposal_layout=true` **without `email`** | render full PDF | `{"template_uid"}` | PDF bytes (~17 MB, `IncompleteRead` happens), stays DRAFT | **V — but path matches the "send" denylist; it is a read only when `email` is absent. Avoid; ask** |
| `POST /estimate/{uid}/send` **with `email`** | **SEND to customer** | `+ email, subject, email_body, attachments:[attachment_uid STRINGS]` (objects silently ignored; the Send dialog pre-ticks every attachment) | stays DRAFT | **V, !!** Then status must be set separately |
| `PUT /estimate/{uid}/status` | status | flat `{"estimate_status":"AWAIT_RESPONSE"}` (docs enum DRAFT, AWAIT_RESPONSE, APPROVED, DECLINED, ARCHIVED, CLOSED, CANCELED, REQUEST_CHANGE; `option_uid` to accept one) | — | V (wrapped body / "SENT" → 400). **!** APPROVED/DECLINED are customer decisions — never set them |
| `DELETE /estimate/{uid}` | delete | — | — | V (deleted test proposals) — forbidden |
| `GET /invoice_estimate/proposal_template?count=10&page=N` | templates (21) | — | `template_uid`, name, options → product refs | V (larger `count` → only 3) |
| `POST /invoice_estimate/proposal_template/{t}/options`, `…/options/{o}/line_items` | template options/items | — | — | V (2026-09-19) — settings write, OK needed |
| `GET /invoice_estimate/package`, `/invoice_estimate/cpq/formulas`, `/invoice_estimate/reminder` | packages, CPQ, quote/invoice **reminders** | — | — | D. Reminders send email/text — never activate |
| `GET /layout_templates`; `PATCH /layout_templates/{u}` / `…/pages/{p}` | proposal layout "DTR Proposal" | — | — | V. A proposal SNAPSHOTS the layout at creation |

Writes to `/estimate*` are on the repo DENYLIST ("quotes, invoices and payments are Zuper's").

---

## 9. Invoices, payments, commissions

| Method & path | Mk / notes |
|---|---|
| `GET /invoice` (`filter.job`, `filter.estimate`, `filter.customer`, `filter.is_paid`), `GET /invoice/{uid}` | V. Fields: number, `invoice_status` (DRAFT/AWAIT_PAYMENT/PAID/…/CANCELED/ARCHIVED), totals, balance, `job.job_uid`, `line_items`. Converting a proposal → invoice puts the option on the INVOICE only |
| `PUT /invoice/{uid}`, `PUT /invoice/{uid}/status`, `POST /invoice/{uid}/send`, `POST /invoice` | D, **denylisted / !!** (status PAID can `send_receipt`, `charge_card`) |
| `GET /invoice_estimate/template`; `PUT /invoice_estimate/template/{uid} {"template":{template_name,template_description,render_engine,type,template_options,template}}` | V |
| `GET /payments/transactions` | V (history pass). Writes denylisted |
| `/payments/payment_request*` | **!! never** (notifies customer; denylisted) |
| `GET /commissions` (`filter.job_uid`, `filter.user_uid`) | V. Create `POST /commissions {job_uid, commission_type:"FLAT_VALUE"\|…, commission_rate, commission_date, assigned_to, description}`; payout `POST /commissions/payouts {commission_uid, payout:{payout_amount,payout_date,remarks}}` (V 2026-10-01) — owner OK only |
| `GET /expenses`; `POST /expenses/category` | D / V (11 categories made 2026-10-27) |
| `GET /analytics/ar_aging`, `/mis/invoice`, `/mis/estimate` | D reads |

---

## 10. Company config, settings

| Method & path | Mk / notes |
|---|---|
| `GET /company/config` | V. `data.zuper_connect.system_notification_number/_enabled`, `jobs.*`, `estimate.*`, `messaging.notify_users`, `company_config_uid` |
| `PUT /company/config {"config_uid":<company_config_uid>,"config":<FULL config minus _id/__v/company_config_uid/company_id/created_at/updated_at>}` | V — full replace. **Many switches send customer messages**; owner OK + safety gate only |
| `GET /misc/quick_filters?filter.module=JOB` (saved views); `POST/PUT /misc/quick_filters` | V (2 views made 2026-09-30) |
| `GET /jobs/delay_alert`, `/jobs/status_alert`, `/jobs/reminder` | V reads. Job reminders/alerts can message customers |
| `GET/POST /request/source {"request_source":{request_source_name}}` (lead sources) | V. Repo `PATHS.lead_sources = /settings/lead_sources` is C/wrong |
| `GET/POST /skills {"skill":{skillset_name,…}}` | V |
| `POST /attachments/default_folders {"folder_name"}` | V (albums) |
| `GET /outbound_logs?filter.type=SMS&filter.module_uid=<customer_uid>` | V — what was texted to a customer (by user, workflow) newest first |
| `GET /user_notifications/count` | V. There is NO API to post into the bell |

---

## 11. Zuper Connect — calls, text conversations (host `https://us-east-1-connect.zuperpro.com/api/telephony`)

The repo's `client.CONNECT_READS` allows exactly these five; everything else on this host is denied.

| Method & path | Body | Response essentials | Mk |
|---|---|---|---|
| `POST /calls` (a SEARCH, it reads) | `{"page":1,"limit":100,"sort":"DESC","sortBy":"created_at","filters":{"current_user":false}}` (docs also: `filters.customer_uid[]`, `module_uid[]`, `direction`, `status[]`, `created_at_from/_to`, `number[]`) | `data[]`: `call_uid`, `direction`, `status` (COMPLETED/MISSED/VOICEMAIL/CANCELED), `duration`, `from_number`, `to_number`, `call_modules[{module:"JOB",module_uid}]`, `call_summary:{status:null,sentiment}` (**no text in the list**) | V 2026-09-30 |
| `GET /calls/{call_uid}/details` | — | `call_recordings[{recording_url, call_summary{summary,next_action,confidence,sentiment,status}}]`. **No transcript field exists** | V |
| `POST /message/conversations` | `{"limit":25,"page":1,"filters":{"status":""}}` (`""` = all; docs: status OPEN/CLOSED required, `response_status` UNRESPONDED, `read_status`, `needs_attention`, `keyword`) | `data[]`: `conversation_uid`, `is_unresponded`, `unread_count`, `recent_conversation_at`, `recent_message{message_uid,direction,message}`, `customer_number{customer_uid,customer_full_name,number,is_blocked}`, `number{number}` (our line), `ai_responder_status`; `total_pages` | V 2026-10-19 |
| `GET /message/conversation/{uid}` | — | conversation detail | V/D |
| `POST /messages` | `{"filters":{"conversation_uid","message_type":["SMS","MMS","CALL","SYSTEM"]},"limit":50,"page":1}` (docs put limit/page inside `filters`; top-level works) | **newest first**: `message_uid`, `message_type`, `direction` INCOMING/OUTGOING, `message`, `status`, `created_at`, `module`, `module_uid`, `user{first_name}`, `call{call_uid,status,duration,call_recordings[…call_summary]}` | V 2026-10-19 |
| `POST /message` | **SEND a text** `{"message":[{phone_number_uid,to_number,message_body,message_type,module,module_uid}]}` | — | **D, !! FORBIDDEN** |
| dial / hangup / anything else | — | — | **!! FORBIDDEN — can ring or text a customer** |

Reading gotchas: workflow/system texts appear as OUTGOING from users labelled "Owen" or "Zuper" — tell
them from a person by wording (`autoreply/rules.TEMPLATE_PHRASES`). `GET /telephony/messages/{id}` → 404.
Our lines: **(954) 420-7373** (primary; system-text sender) and (786) 882-0190 (outgoing only). Quo
(OpenPhone) calls/texts are NOT in Zuper — they are in the CRM (`ZUPER-OPERATIONS.md` §8a).

Team chat ("Messaging & Chats" in docs) is Stream (`/stream/message/send`) — **D, !! do not send**.

---

## 12. Webhooks (host path WITHOUT `/api`)

| Method & path | Mk / gotchas |
|---|---|
| `GET https://us-east-1.zuperpro.com/service/notifications/webhook?count=&page=` | **V.** Repo `PATHS.webhooks` puts it under `/api` → 404 (why "Zuper did not list webhooks"). Docs also imply `/api` → **X** |
| `POST …/service/notifications/webhook {"web_hook":{webhook_name,webhook_event,webhook_url,content_type,request_method,webhook_module,headers:<DICT>,is_active:true}}` | V 2026-10-12. Headers as a string get double-encoded. Docs say `POST /webhook` (repo `webhook_create`) — never used live |
| `PUT …/service/notifications/webhook/{uid}` | **V: without `is_active:true` it DEACTIVATES the webhook.** Docs say update = `POST /webhook/{uid}` |
| `GET …/service/notifications/webhook_history[/{uid}]` | V (includes `request_body`) |
| Event names | **lowercase dotted** (V): `job.new`, `job.schedule`, `job.update_schedule`, `job.status_update`, `job.update_checklist`, `job.new_note`, `job.delete_note`, `job.new_attachment`, `sms.new_sms`, `call.ended`, `measurement.new`, `estimate.*`, `customer.*`, `project.new_attachment`, `purchase_order.status_update`. Repo `webhook.EVENTS` uses `JOB_NEW` style → **wrong**; docs list none |
| `sms.new_sms` payload | only `{message_uid, from_telephony, triggered_by, triggered_at, event}` — no text; fetch it via `POST /message/conversations` |
| Signing | none documented; the CRM endpoint `/api/zuper/webhook` uses a shared token. **No webhook to the CRM exists; none has ever arrived** |

---

## 13. Workflows (host `https://us-east-1-workflow.zuperpro.com/api`)

| Method & path | Mk / gotchas |
|---|---|
| `GET /workflows?filter.is_active=…`, `GET /workflows/{uid}` | V. The REST `/api/workflow` (docs) lists only 2 legacy ones — use the workflow host |
| `PUT /workflows/{uid}?publish_to_live=true {"workflow":{name,description,nodes,connections,meta_data,is_active}}` | V; republishing makes new node uids |
| `PUT /workflows/{uid}/activate` / `/deactivate` | V. **!! Activating = automatic customer messaging + breaks `zsafety.py` (stops routes/tasks/proposals) unless allow-listed** |
| `POST /gatekeeper/workflow/{uid}/test {"node_uid":<last node>}` | **!! A test run SENDS FOR REAL** (uses DRAFT; trigger needs `is_pinned`+`pinned_data`) |
| `GET /workflows/execution_history?filter.workflow_uid=&page=&limit=`; `GET /workflows/{wf}/executions/{ex}/nodes/{node_uid}` | V reads (`request_data` = exactly what was sent). No API stops a WAITING run (404) |
| `GET /nodes?count=10&page=N` | V node catalog |
| Gotchas | `sms.incoming` trigger never fires here (use webhook `sms.new_sms` → `/gatekeeper/workflow/webhook/<uid>` + External Webhook start step); `internal_notification` (bell) step fails ("Cannot find module"); Notify Customer `node_lookup` must be the trigger node uid; code-step JS sandbox differs from Node (avoid regex) |

---

## 14. Repo DENYLIST (checked first, any method) — Owen AI must apply the same

| Pattern | Why |
|---|---|
| `^/telephony(/\|$)` | Zuper Connect places real calls / sends texts (only the 5 reads in §11 are carved out) |
| `^/(sms\|messages?\|conversations?\|chat)(/\|$)` | messaging reaches a customer |
| `(^\|/)send(_[a-z]+)?(/\|$)` | any send emails or texts a customer (`/customers/{uid}/send` only inside approved scopes) |
| `^/payment_requests?(/\|$)\|(^\|/)payment_link(/\|$)` | payment request notifies a customer |
| `portal\|invite` | portal invite emails a customer |
| `^/service/notifications/(?!webhook(/\|$\|_history))`, `^/notifications?(/\|$)` | notification send |
| `(^\|/)reminders?(/\|$)` | reminder to a customer |
| `(^\|/)(email\|emails)(/\|$)` | email endpoint |
| WRITES to `^/(estimates?\|quotes?\|proposals?\|invoices?\|payments?\|credit_notes?\|payment_modes?)(/\|$)` | money documents are read-only for the CRM |

Repo ALLOWLIST (what the CRM may call at all): GET user/user-all/customers/categories/statuses/jobs/job/notes/attachments/folders/service_tasks/appointments/estimate/invoice/payments/commissions/product/activities/company_config/custom_fields/lead_sources/webhooks; writes only within the scopes in §0.

---

## 15. Doc vs repo-observed discrepancies (repo-observed wins)

1. Job create: docs nested `customer{}` object → live needs **`customer_uid`** + mandatory **`due_date`**.
2. `PUT /jobs` with `custom_fields` / `products` **replaces the whole list** (docs silent); scheduled times in `PUT /jobs` are **ignored** (docs list them). Use `PATCH /custom_fields` (merge) and `PUT /jobs/{uid}/update` (type SCHEDULE, undocumented).
3. Webhooks: real path has **no `/api`**; create/update live = `POST/PUT /service/notifications/webhook[/{uid}]` (docs: `POST /webhook`, `POST /webhook/{uid}`); PUT without `is_active:true` deactivates; event names are dotted lowercase (repo constant wrong; docs none).
4. Service tasks list: docs + live `/service_tasks?filter.module_uid=`; repo `/jobs/{uid}/service_tasks` → 404.
5. Job attachments: docs `/jobs/{uid}/attachments`; live GET 404 — photos are on notes.
6. Status create: wrapped `{"job_status":{…}}` works and returns no uid; flat "succeeds" and creates nothing; repo tries flat first. Status edit: full record to `PUT /jobs/status/{cat}`; repo's `/jobs/status/{cat}/{status}` partial = unverified.
7. Category create body: DECISIONS (09-17, flat accepted) vs memory (09-23, `{"category":{…}}` worked, flat refused). Use the wrapper; flag.
8. Note: docs `notify_users` is a query param; repo sends it in the body (works, but notification suppression via body is unproven). Note edit: docs `PUT …/note/{n}/update`; repo uses `PUT …/note/{n}` (= docs "change privacy").
9. Recover: docs `POST /jobs/recover`, `POST /recover`; repo `POST /jobs/{uid}/recover`, `/customers/{uid}/recover` — both untested.
10. Appointments module documented but **403** on this account.
11. Custom-field definitions query: repo `?module=`; web app `?module_name=`.
12. Lead sources: repo `/settings/lead_sources`; live `/request/source`.
13. Connect conversations: docs require `filters.status` OPEN/CLOSED and put `limit/page` in filters for messages; live `status:""` and top-level `limit/page` work.
14. `/customers/{uid}/send`: docs show optional `sms.from`; memory says no from parameter (sender = System Notification Number). Untested — never rely on `from`.
15. Rate limit: docs 200–700/min by plan; repo assumes 200 (paces at 180). Actual plan limit unknown — read `X-Rate-Limit`.
16. `POST /estimate/{uid}/send` without `email` renders a PDF and sends nothing (live) — docs describe only sending.
17. Repo docstrings call most shapes "UNVERIFIED" (2026-09-16); many were verified later only in docs/memory — the markers here are the up-to-date status.
