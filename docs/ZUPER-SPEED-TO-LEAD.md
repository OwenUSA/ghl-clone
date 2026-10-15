# Speed to lead in Zuper — what is possible (checked 2026-10-15)

The detailed speed-to-lead report lives in the **CRM**: Reporting → **Speed to lead**, and the
two "Speed to lead" sheets of the nightly KPI Excel. It is built from every call and text on
Quo, the CRM line and Zuper Connect (`backend/app/dispatch/speed.py`, DECISIONS.md 2026-10-15).

This page is the Zuper side: what Zuper itself can show, checked **read only** on 2026-10-15
(GET requests and Zuper's public web-app code; nothing was created, saved, generated or
switched on). Everything below that needs a click in Zuper is a **write**, so it needs the
owner's OK under the add-only rule (`ZUPER-OPERATIONS.md` section 1). None of it has been done.

---

## 1. The short answer

**Zuper cannot show an average speed to lead today without something being created in it.**

- Zuper *does* record the number underneath: every entry in a job's status history has
  `time_on_status`, in whole minutes (VERIFIED on a live AHS - Inspection job: 14 minutes in Work
  Order Received, 64 in Welcome Call!).
- But no screen uses it: no built-in report that can be confirmed without generating it, no
  Advanced Reports field, no dashboard widget (VERIFIED in the report metadata and the web-app
  code).
- Advanced Reports cannot subtract two dates, and it averages only **number** fields. All 79 job
  custom fields on the account are text.
- Zuper never sees most of the evidence anyway: it does not know about Quo calls, and the column
  move is only a stand-in for "we called". On production it came a median 2.5 business minutes
  after the first call, but in 1 job in 4 it came more than 2 hours later.

So Zuper can show a **live queue** of who has not been contacted and a **count of breaches**,
but not the time. The CRM shows the time.

## 2. The options, simplest first

| Option | What the team sees in Zuper | Exact? | What it takes |
|---|---|---|---|
| A. Saved view "Not contacted yet" | the jobs still in the first column, oldest first | a live queue, no time | one saved view (a write) |
| B. Delay alert, 2 hours | jobs that stayed in Work Order Received > 120 min flagged **Delayed**; the dashboard's "Delayed Jobs" widget counts them | a breach count | one delay alert (a write) |
| C. "Track Time in Status" on the first column | the board card is marked when a job sits past 120 min | visual only | a column setting (a write) |
| D. Built-in "Job status history" report | an export per job; maybe with time per status | unverified | generating it creates a report-history record |
| E. A number job field written by the CRM | "Speed to lead (min)" on every job, averaged by month in an Advanced Report | exact | a new field + the owner lifting the read-only rule for one more CRM write |

### A. A saved view: "Not contacted yet"

The filter operators were verified (`GET /jobs/meta/filter`); saving the view is the write.

1. Zuper → **Jobs** → **Filter**.
2. Job Category **contains** `AHS - Inspection` (add `Retail` and the regional boards for a
   second view).
3. Job Status **contains** `Work Order Received` (Retail: `New Lead`).
4. Sort by **Created On**, oldest first. **Create New View**, name it "Not contacted yet".

Every job in it has not been moved since it arrived. The oldest at the top is the most overdue.
Whether "Created Date less than" can be a rolling "older than 2 hours" is NOT verified, so the
view shows all of them, oldest first.

### B. A delay alert — the closest thing to a KPI inside Zuper

Zuper's delay alerts can watch "from status X to status Y within N minutes" and flag the job as
**Delayed** (VERIFIED, `GET /jobs/delay_alert`). One already exists, **inactive**, as a template:
"Flag job delayed - SLA 5min" on the unused Lead Qualification board.

A copy for AHS - Inspection would be: from **Work Order Received** to **Welcome Call!** within
**120** minutes → flag the job delayed. Then:

- Jobs list filter **Job Delayed = Yes** lists the breaches.
- Dashboard → Customize → **Delayed Jobs** counts them.

**Before switching it on, ask the owner, and check two things on test job #701:** that the alert
does NOT notify customers (it must send to staff only, or nothing; the safety gate fails on any
customer-facing alert), and whether the Delayed flag clears once the job moves (not verified).
Its minutes are clock minutes, not business hours, so a work order that arrives at 9 PM will be
flagged overnight.

### C. "Track Time in Status"

A per-column setting (Settings → Jobs → Job Category Hub → the board → the column) with an
estimated duration. A job that sits past it gets a mark on its board card. It is on today for
"Submit To AHS For Approval" and "Awaiting AHS Payment" only. Setting it to 120 minutes on Work
Order Received (and 60 on New Lead) makes overdue leads visible on the board. It feeds no report.

### D. The built-in "Job status history" report

Reports → Job → **Job status history**, Job Category = AHS - Inspection, "Generate report by" =
created date, a month range, **Generate Report**. The filters were verified. Its columns are
produced on Zuper's server and were NOT seen, because generating creates a record. If the export
has a time-in-status column, the average for Work Order Received can be taken in Excel. Nothing
can be filtered by status inside the report.

### E. An exact number inside Zuper (needs a rule change)

1. Add ONE job field (append-only endpoint, as with the KPI questions): `Speed to lead (min)`,
   **Number**, in the KPI group, on AHS - Inspection and Retail.
2. The CRM writes each lead's business minutes into it once the first response is known.
3. Advanced Reports → New report → **Jobs** → **Summarized** → filter Job Category → rows by
   **Created At, Month and Year** → total **Average** of `Speed to lead (min)`.

Step 2 is a NEW write to Zuper: the read-only rule (2026-09-27) forbids it unless the owner lifts
it for this one thing. Writing a job field is also the dangerous `PUT /jobs`, which **replaces the
job's whole field list** (`ZUPER-OPERATIONS.md` section 8), so it would need the full-list write
and a read-back. Whether Advanced Reports treats a number custom field as a number is inferred,
not proven.

## 3. Recommendation

1. Use the **CRM report** as the KPI. It is the only one that sees Quo and Zuper calls, knows
   business hours, and says who answered.
2. In Zuper, ask the owner for **A** (the queue) and **B** (the 2-hour delay alert). They are
   small, add-only, and give the team a live list and a breach count where they already work.
3. Ask about **E** only if the owner wants the exact number on the job page in Zuper.

## 4. Evidence

Raw metadata answers (no customer data) and the GET-only helper are on dispatch in
`/tmp/stl_check/` (scripts, `out/`, `bundles/`). Endpoints read: `/reports/meta`,
`/reports/meta/filters`, `/reports-service/v3/attributes?data_source=JOB`,
`/reports-service/v3/meta/filter?module=JOB`, `/jobs/meta`, `/jobs/meta/filter`,
`/jobs/delay_alert`, `/analytics/widgets`, `/jobs/status/{category}`, one `/jobs/{uid}` (the
sample was deleted after reading `time_on_status`).
