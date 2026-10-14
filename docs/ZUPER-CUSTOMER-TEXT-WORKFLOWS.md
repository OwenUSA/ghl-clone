# Zuper customer-text workflows — reminders, column texts, thank-yous (LIVE since 2026-10-13)

Six Zuper **Workflow Builder** workflows text AHS customers automatically from the primary line
**(954) 420-7373**. They were built and tested on 2026-10-12/13 and switched live on
**2026-10-13 16:41 New York** at the user's request ("ok activate").

They live **in Zuper** (Workflows page), not in this repo. The CRM's own automatic texts
(`backend/app/reminders/`) are OFF since 2026-10-12 and must stay off, or customers get two
texts. Read `ZUPER-OPERATIONS.md` section 1 (the owner's rules) first.

Nothing here contains customer data. Message wording below uses a sample customer.

---

## 1. What runs

| # | Workflow (Zuper name) | uid | Starts on | Sends |
|---|---|---|---|---|
| 1 | DTR - AHS reminder - 1 day before | `d4f30597-d393-4d49-a5a8-e21b7bb2635b` | a job is scheduled / rescheduled | day-before reminder; then a "not confirmed" alert check |
| 2 | DTR - AHS reminder - 2 hours before | `79b9a4e4-05cc-464d-98f5-7c151aa51a90` | a job is scheduled / rescheduled | 2-hour reminder; then a "not confirmed" alert check |
| 3 | DTR - AHS text - Inspection Completed | `cffbe8da-1940-4bc4-9541-18b85e26153d` | job moves INTO that column | rate the technician 1-5 |
| 4 | DTR - AHS text - Awaiting AHS Decision | `5a41a844-cc97-494e-b676-3be55d646efd` | job moves INTO that column | report submitted to AHS |
| 5 | DTR - AHS text - AHS Approved | `ad2e937e-1e86-4f93-9672-142cc777b5f2` | job moves INTO that column | repair authorized, we will call |
| 6 | DTR - Thank you for confirming | `79afb5bb-9272-427c-8a12-c3487b55f6a2` | an incoming text (via webhook) | thank-you for a "yes" or a rating; alert on any other reply |

Plus one Zuper **webhook**: "DTR - Thank you for confirming (incoming SMS)"
`5c798249-a7c9-4de9-98de-c2e8e6ef8e7d`, event `sms.new_sms` → workflow 6.

Boards: reminders (1, 2) on **AHS - Inspection** (inspection wording) and **AHS - Repair & Review**
(repair wording). Column texts (3-5) on **AHS - Inspection** only. Retail and the regional boards
are not covered.

All the other 41 workflows in the account (Zuper templates, the two September "DTR - Appointment
reminder" drafts) and all 21 customer-notification rules are **off** and must stay off.

## 2. The messages (exact wording)

Reminders (1, 2):

- 1 day, inspection: `Hi Mary, this is Dream Team Roofing. Reminder: your roof inspection is tomorrow, Wednesday, October 14, between 1 PM and 3 PM at 123 Main St, Bradenton. Need to reschedule? Call or text (954) 420-7373. Please reply "YES" to confirm the appointment. Reply STOP to opt out.`
- 1 day, repair: same, `your roof repair is scheduled for tomorrow, ...`
- 2 hours, inspection: `Hi Mary, Dream Team Roofing here. Our technician is on schedule to arrive today between 1 PM and 3 PM at 123 Main St. Questions? Call or text (954) 420-7373. Please reply "YES" to confirm the appointment. Reply STOP to opt out.`
- 2 hours, repair: `... Our crew is on schedule to arrive today between ... for your roof repair at 123 Main St. ...`

Column texts (3-5):

- Inspection Completed: `Hi Mary, thanks for choosing Dream Team Roofing. How was your inspection today? Reply 1 to 5 (5 = excellent) to rate our technician. Reply STOP to opt out.`
- Awaiting AHS Decision: `Hi Mary, this is Dream Team Roofing. We have submitted your inspection report to American Home Shield. We'll let you know as soon as AHS makes a decision or we have any update. Questions? Call or text (954) 420-7373. Can you confirm you received this message? Reply YES or NO. Reply STOP to opt out.`
- AHS Approved: `Hi Mary, good news from Dream Team Roofing: American Home Shield has authorized your roof repair. A member of our team will contact you within 1 business day to schedule it. Questions? Call or text (954) 420-7373. Can you confirm you received this message? Reply YES or NO. Reply STOP to opt out.` ("authorized", never "approved" — Owen's rule; no amount is ever shown.)

Thank-yous (6):

- after a yes to a reminder: `Thank you, Mary! Your appointment is confirmed. See you then! - Dream Team Roofing`
- after any reply to the rating request: `Thank you for your feedback, Mary! We appreciate you choosing Dream Team Roofing.`

The wording is in each workflow's **Notify Customer** step (1-5) or the "Decide" code step (6), and
in the builder scripts (section 6). Change both, or a rebuild undoes the edit.

## 3. The rules

**Texting window: 8 AM - 8 PM New York, enforced twice** (when planning, and again right before sending).

Reminders (1, 2):
- 1 day before = visit - 24 h. Before 8 AM → 8:01 AM that day; after 7:50 PM → 7:50 PM.
- 2 hours before = visit - 2 h. Before 8 AM → 8:01 AM if that is still ≥ 30 min before the visit, else skipped. After 7:50 PM → skipped.
- A reminder whose time has already passed (visit booked late) is skipped.
- **Rescheduling works by itself:** every schedule change starts a new run; the old run re-reads the job just before sending, sees the time moved, and sends nothing. Verified live.
- Final check before sending: same appointment as planned; still on an AHS board; status not Cancelled / Reschedule Required / Estimate Declined / completed / Paid; customer has a mobile number.
- Open question (2026-10-13): jobs in non-visit columns (e.g. "Invoice Submitted to AHS", "Call Back") that still carry a future date DO get reminders. Restricting reminders to visit columns was proposed, not decided.

Column texts (3-5):
- Only a move INTO the column, on AHS - Inspection. **Once per job**: if the job's history already has that column, nothing is sent.
- Moved between 8:01 AM and 7:50 PM → sent ~1 minute later; otherwise at 8:01 AM.
- Not sent if the job is Cancelled / Estimate Declined by then.

Thank-you (6) — **regular conversations on the 954 line are left alone**:
- Acts only when the LAST text we sent that customer is a reminder asking to "confirm the appointment" (≤ 48 h) or the rating request "to rate our technician" (≤ 72 h). A text from staff after that (e.g. Luis) ends the automation for that customer.
- Texts sent by staff never start it; only incoming texts to (954) 420-7373 from a number that belongs to exactly one Zuper customer.
- Reminder context: a short "yes" (yes, y, ok, okay, yeah, yep, sure, confirmed, sí, thanks, thank you, 👍 …; any no / not / can't / reschedule / cancel / change wins) → thank-you. Anything else → "replied to a reminder" alert (section 4).
- Rating context: any reply except STOP → feedback thank-you. The 1-5 number is read (`rating` in the run log) but **not stored anywhere**.
- One reply only: after our thank-you, our thank-you is the last text, so further texts get nothing.
- Outside 8 AM - 8 PM: no thank-you at all.
- STOP / UNSUBSCRIBE are left to the carrier.

## 4. Alerts to Luis and Owen — currently NOT delivered

The user's decision (2026-10-13): alerts go to the **Zuper bell only, no emails**.

- 1 and 2: 1 hour after the reminder (alerts only 8 AM - 8 PM; later → 8:00 AM next day), if the customer has not confirmed (= no thank-you went out after the reminder) and the visit is unchanged → "Appointment not confirmed: <name> has not confirmed the roof inspection on <day> at <time>. Please call them at <phone>."
- 6: a non-yes reply to a reminder → "Customer replied to a reminder: <name> replied ... Please answer them at <phone>."

Both are an **Internal Notification (PUSH)** step "Alert Luis + Owen (bell)" to Luis Candiales
(`4369d420-89d1-40e4-96aa-9091d96fb94b`) and Owen Buzaglo (`74cd936c-9386-469b-addc-d05d3d1a06a0`).
**That step fails on Zuper's servers**: `Cannot find module '/home/jenkins/nodes/internal_notification'`
(the newer `zuper_generic_internal_notifications` is not offered to this account). The user chose to
leave it failing until Zuper fixes it — runs show the alert step as FAILED; nothing reaches anyone.
Until then Luis must watch the 954 inbox for "No" / questions.

Alternatives investigated (not in use):
- **Send Email** step (`zuper_email`, master `7cc686f1-66ec-403d-9fc4-3261bafcfc00`) works — it was live for a day, removed at the user's request.
- **A task assigned to Luis/Owen** (`POST /service_tasks?notify_users=true`) does ring their bell, but the company setting `jobs.auto_add_task_assignee = true` ALSO adds the assignee to the job (ignores `assign_users_to_module:false`) — Owen is a technician, so that would touch routes. Would need that setting off (owner's call).
- No API posts into the bell directly (only `/user_notifications/count` exists).

## 5. How it is wired (what a new session must know)

**Sender.** Zuper sends system texts from the company's "System Notification Number": Settings →
Communication → Message Settings, set by the user to **19544207373** with system notifications ON
(was 806-304-9902, OFF). `/customers/{uid}/send` has no from-number. Verified on a phone: texts arrive
from (954) 420-7373.

**Safety gate.** `zsafety.py` blocks every script that writes to Zuper when anything customer-facing
is on. It was changed (three copies: dispatch `/home/qa/zsafety.py`, owen-main
`/opt/santiagoproperties/zuper-tasks/app/zsafety.py` and `.../zuper-proposals/app/zsafety.py`; the two
containers must be rebuilt with `docker compose up -d --force-recreate` after any edit) to allow:
- `zuper_connect.system_notification_enabled` ON **only** with sender `19544207373`;
- exactly these six workflows active (`APPROVED_WORKFLOWS`). Any other active workflow still fails the gate.
Backups `zsafety.py.bak-20261012`, `-20261012b/c/d`.

**Incoming texts (workflow 6).** The "Inbound text message" trigger NEVER fires on this account.
Incoming texts only reach a workflow through a Zuper webhook (`sms.new_sms`) pointed at
`https://us-east-1-workflow.zuperpro.com/api/gatekeeper/workflow/webhook/<workflow uid>`, and the
workflow must start with an **External Webhook (POST)** step — with any other start step the
gatekeeper answers 400 "Invalid Webhook Method". The delivery carries only
`{message_uid, triggered_by, triggered_at, event}`, so the workflow's first step reads
`POST https://us-east-1-connect.zuperpro.com/api/telephony/message/conversations`
(read-only, newest first) and takes the conversation whose `recent_message.message_uid` matches.
(The old webhook "Trigger when new SMS receieved" points at the inactive Zuper template 31c7d57a and
gets 404 on every text — harmless, left alone.)

**Zuper quirks that cost time:**
- A **test run** (`POST {workflow host}/gatekeeper/workflow/{uid}/test {"node_uid": <last step>}`, trigger needs `is_pinned` + `pinned_data`) **sends real texts**. A CALENDAR_DATE Wait does hold a test run until its time.
- Publishing: `PUT /workflows/{uid}?publish_to_live=true {"workflow": {...}}`; activating needs a published version (`PUT /workflows/{uid}/activate`). A test run uses the DRAFT; republishing gives new step uids.
- Notify Customer's `node_lookup` must be the **trigger** step's uid (`POST /workflows` accepts a wrong value; `PUT` then refuses "Please select a Zuper Node").
- Zuper's code-step sandbox evaluates some JavaScript differently from Node: a regex/normalize yes-check read "Yes" as not-yes. Keep code steps to plain string/array logic and log what was decided.
- Moving a job by API with checklist answers: `PUT /jobs/{uid}/status {job_uid,status_uid,status_name,customer_signature:"",remarks,checklist:[{question,answer,type}]}` — answers must be strings; required photo/video questions are not enforced by the API.
- Webhooks: create `POST https://us-east-1.zuperpro.com/service/notifications/webhook {"web_hook":{...,"headers":<dict>,"is_active":true}}`; a `PUT` without `is_active:true` switches it OFF; headers as a string get double-encoded. Delivery log: `GET /service/notifications/webhook_history[/{uid}]`.
- Execution log: `GET {workflow host}/workflows/execution_history?filter.workflow_uid=&page=&limit=`, per step `GET /workflows/{wf}/executions/{ex}/nodes/{step uid}`. What was texted: `GET /api/outbound_logs?filter.type=SMS&filter.module_uid=<customer uid>`.

## 6. Scripts and backups (dispatch, not in git)

Folder `/home/qa/zbrowser/ahs_reminders/` (run as root with `python3 -I`, helper `../zwf.py`):

| Script | Does |
|---|---|
| `build_ahs_reminders.py` | builds/updates workflows 1-2 (shared helpers, New York time code, `TEXTS`) — refuses active workflows; dry run default |
| `build_ahs_stage_texts.py` | workflows 3-5 |
| `build_ahs_thanks2.py` + `republish_thanks_and_test.py` | workflow 6 (`--test` re-runs Santiago's "Yes"; without it just republishes) |
| `add_not_confirmed_alert.py [--two-hours] --apply` | added the 1-hour "not confirmed" branch to 1 / 2 in place |
| `alerts_to_bell.py` | turned the alert steps into the bell step (2026-10-13) |
| `apply_texts.py --apply` | pushes the builders' current wording into the live workflows (Notify steps only) |
| `resched.py <minutes>` / `resched734.py`, `move_test.py` / `move734.py`, `read_run.py`, `read_thanks.py` | tests on the test jobs (below) |

**Builders regenerate step uids and still contain `TEST_MODE = true` and the e-mail/bell history —
read a script before re-running it on a live workflow.** Go-live was done by editing the live code
in place (`TEST_MODE = false`), not by the builders.

Backups `/root/backups/`: `workflows_before_*`, `wf_<uid8>_before_<change>_<time>.json` (one per change),
`zuper_webhooks_before_thanks_*.json`. Go-live: `wf_*_before_golive_20261013-224110.json`.

## 7. Test customers and jobs

- **Santiago Villahermosa (TEST)** `2dbd7187-a6e8-4452-8600-c3d029599d75`, mobile 561-878-8090 (the user's phone) — job **#701** `6abd527f-31a6-43e9-bb0a-6fe557dac596`, AHS - Inspection.
- **Owen Test** `9568609a-c6ca-4d9a-a882-b3ef858c6c76`, 561-690-5516 (Owen's phone) — job **#734** `2f14b7ff-7f94-4cc1-8aa2-dd62b067a6e7`, AHS - Inspection.
- Never use +15618999051 (not ours; it once answered QUIT).
- Quick live test: `python3 -I resched.py 123` (#701 visit in 2 h 3 min → 2-hour reminder in ~3 min), reply "Yes" from the phone → thank-you within seconds.

Verified live on 2026-10-12/13: both reminders on time and from 954; reschedule → old reminder
cancels, new one sent; the three column texts; yes → thank-you; rating "5" → feedback thank-you;
no reply → "not confirmed" alert fired (by email at the time); staff texts ignored.

## 8. How to stop or change it

- **Stop one workflow:** Zuper → Workflows → switch it off (or `PUT {workflow host}/workflows/{uid}/deactivate`). Takes effect at once for new runs.
- **Back to test-only:** in the workflow's "Final check" (1-5) or "Decide" (6) code step set `const TEST_MODE = true;` (the `TEST_CUSTOMERS` list is right below) and publish.
- **Stop everything customer-facing:** deactivate all six; optionally Settings → Communication → Message Settings → system notifications off (zsafety then passes again either way).
- Adding a new workflow that texts customers: build it OFF, test with the test customers, add its uid to `APPROVED_WORKFLOWS` in all three zsafety copies, rebuild the containers, then publish + activate.

## 9. Open items

- [ ] Restrict reminders to visit columns (Scheduled / Day-Before Call / Same-Day Confirm / Repair Scheduling…)? Asked 2026-10-13, no answer yet. #410 (Invoice Submitted to AHS) and #720 (Call Back) had future dates at go-live.
- [ ] Visits booked BEFORE go-live (#656, #410, #720, #724 Wed 10/14; #627 Fri 10/16) may not get reminders (their queued runs started in test mode) — check the outbound log Wednesday; re-saving the appointment in Zuper starts fresh reminders.
- [ ] Bell alerts fail until Zuper fixes `internal_notification` — report it to Zuper support, or pick an alternative (section 4).
- [ ] Store the 1-5 technician rating somewhere (job field / note), and/or alert on ≤ 3.
- [ ] Retail and the regional boards are not covered; Spanish not built (user: no Spanish).
