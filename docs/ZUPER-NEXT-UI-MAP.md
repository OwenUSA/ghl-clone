# Zuper's new UI (`next.zuperpro.com`) — map (2026-10-05)

What every module and settings page of the new Zuper web app is, where it lives, and which API
request is behind it. Made by a **read-only** headless crawl as the admin user: about 300 routes
visited, plus tabs and read-only menus on the pages the business uses. Nothing in Zuper was
created, changed or deleted, and this file holds **no customer data**.

Rules for working in Zuper are in `ZUPER-OPERATIONS.md` section 1; they apply here unchanged.

---

## 1. What is new compared with `ZUPER-OPERATIONS.md`

Things the new UI has that the operations doc does not mention:

| New | Where | State on this account |
|---|---|---|
| **Sense** — Zuper's AI assistant (chat threads, "radars", suggestions) | `/sense/chat` | available |
| **Projects** (a job container with its own categories, statuses, Kanban, Gantt, progressive invoicing) | `/projects` | on; 2 projects, both tests |
| **Photo Feed** (every job photo, by date or by job; tag, download) | `/photo_feed` | in use |
| **Production** menu: Material Requests, Material Orders, Suppliers, Transfer Orders | `/material_requests`, `/purchase_order`, `/vendors`, `/transfer_orders` | empty |
| **Commissions** | `/commissions` | 60 rows |
| **Payments** (Zuper Pay dashboard, payouts, 6 payment reports) | `/payments/dashboard` | Zuper Pay not set up |
| **AR Management** (overdue invoices, aging summary) | `/invoices/ar_aging/overview` | available |
| **Advanced Reports** (a report builder: folders, templates, schedules, history) | `/advanced_reports/all` | no reports made yet |
| **Communication** — one inbox for Connect conversations and email | `/connect/conversations`, `/connect/emails` | no mailbox connected |
| **CSR Agents** (AI phone agents for Zuper Connect) | Settings → Communication | none created |
| **Zuper IQ** — proposal templates, Formula Library (118 formulas), Service Packages, Financing | Settings → Quotes & Invoices | templates exist (Zuper samples + DTR) |
| **Measurements** (tokens; Hover / EagleView / RoofScope / GAF providers) | Settings → Jobs | no provider connected |
| **Supplier integrations** (SRS, ABC Supply, QXO) | Settings → Parts & Services | not connected |
| **Data Migration** (CompanyCam, AccuLynx, JobNimbus importers) and **Recovery** (deleted jobs, 90 days) | Settings → Data Administration | available |
| **Website Widgets** (booking widget for a website) | Settings → Customer Experience | none |
| **Custom Functions**, Webhooks, API keys, Sync History | Settings → Developer Hub | no custom functions |
| **Zuper Labs** — one experiment: "New Job Creation (Beta)" | Settings → Zuper Labs | **Off** |
| **Layout Customization** (the job page's layout) | Settings → Jobs | 1 layout, "Job Details Default" |
| **Saved views** on every list ("Create New View") and a Kanban view for jobs and projects | list pages | Jobs has 2 saved views |

Renamed in the new UI: Estimates → **Quotes**, Products → **Parts & Services**, Purchase Orders →
**Material Orders**, Vendors → **Suppliers**, Business Units → **Trade Types**, Settings lives at
`/settings_new/...`.

**Not present on this account** (the route exists in the app but sends you back to the dashboard
or Settings home): Recurring Jobs, Organizations, Assets, Contracts, Requests, Service Orders,
Subcontractors, Financing, Playbooks, Field Recordings, Customer Portal settings, SSO, App Store
page, Customer Experience page.

---

## 2. The menu

Left bar, top to bottom:

| Menu | Opens |
|---|---|
| **Dashboard** | `/dashboard` |
| **Sense** | `/sense/chat` |
| **Work** | Projects `/projects` · Jobs `/jobs` · Photo Feed `/photo_feed` · Chats `/chats` |
| **Schedule** (icon reads "Dispatch Board") | Calendar `/calendar/week` · Dispatch Board `/dispatch_board` · Maps `/maps` |
| **Production** (Beta) | Material Requests · Material Orders · Suppliers |
| **CRM** (icon reads "Customers") | Customers `/customers` · Properties `/property` |
| **Accounting** | Quotes `/estimates` · Invoices `/invoices` · Payments `/payments/dashboard` · Commissions `/commissions` |
| **Communication** | `/connect/conversations` |
| **More** | Catalog · Timesheets · Reporting (three sub-menus, not opened by the crawl) · Workflows `/workflows` · Settings `/settings_new/home`. The routes that belong under them: Parts & Services `/products`, Item Groups `/product_groups`, Pricelists `/pricelists`, Transfer Orders `/transfer_orders`; `/timesheets`, `/timesheets/grid`, `/timesheets/timeoff_requests`, `/shifts`; `/reports`, `/advanced_reports/all` |

Top bar, every page: **New** (Job · Customer · Quote · Invoice · Broadcast), global Search
(Ctrl+K, `/search`), Tasks (Beta) drawer, Broadcast, Inbox (Chats / Emails), Notifications, and
the profile menu (Profile, Mute notifications, App Store, Help, Product updates, Report a Bug,
Log Out).

The Zuper Connect dialer still floats over the right side of the page (see the memory note on
forms that "won't submit").

---

## 3. Workspace modules

Every list page works the same way: saved views across the top ("All …", "Create New View"),
**Filter** (rules joined by AND / OR, pinnable), **Columns**, rows per page, Export, and bulk
actions that appear once rows are ticked. Each list is loaded by one `POST /api/<module>/filter`
with `{limit, page, filter_rules, sort, sort_by}` — a read, despite being a POST.

### 3.1 Jobs — `/jobs`

- **List columns:** Job ID, Job Title, Customer, Users / Teams Assigned, Category, Service
  Address, Status, Priority, Scheduled Date, Due Date, Tags, Created On.
- **Views:** All Jobs, plus the two saved views *AHS Inspection - live* and *AHS Repair - live*.
  List or **Kanban**. Quick filters: Job Category, Scheduled Date Range, Job Priority. Customize:
  Compact / Comfortable, Customize columns.
- **Bulk actions:** Update Field, Update Schedule, Update Job Status, More actions, Export.
- **API:** `POST /api/jobs/filter` · `GET /api/jobs/meta`, `/api/jobs/meta/filter` ·
  `GET /api/misc/quick_filters?filter.module` (the saved views) · `GET /api/settings/custom_fields`.

**Job page** — `/jobs/<job_uid>/details`

- Header: Print/Share, More Actions, Change Cover, **Update Status**, **Schedule**, Call, Mail,
  Add Note.
- Left panel: Primary Details (Trade Type, Category, Priority, Assigned To, Due Date, Customer,
  Lead Source, Tags, Skills, Job Type, Parent Job, created / updated), then each job-field group
  shown on that board (e.g. Checklist, AHS, KPI).
- Tabs: **Overview**, Tasks, Line Items, Notes, Documents, Accounting, and "+6 More"
  (customisable with "Customize Tab").
- Overview: the **Progress Tracker** (Lead → Estimating → Sold → Production → Account
  Receivable → Completed), Highlights (Due Date, Job Value, Tasks, Profit), Open Tasks,
  Description, Address + map, Recent Invoices, Recent Documents, Gallery, Job Checklist (one row
  per status move, "View Checklist"), and associations (Assigned To, Customer, Secondary
  Contacts, Property, Project, Child Jobs).
- **API:** `GET /api/jobs/<uid>` · `/api/jobs/status/<category_uid>` · `/api/service_tasks?filter.module_uid` ·
  `/api/notes?filter.job` · `/api/documents?filter.module_uid` · `/api/attachments/group` ·
  `/api/invoice?filter.job` · `/api/measurements?filter.job_uid` · `/api/material_requests?filter.job` ·
  `/api/purchase_orders?filter.job` · `/api/messaging/message?job_uid` · `/api/activities/recent`.
- **Opening a job page makes the browser try one write by itself:**
  `POST /api/meta_data {"meta_data":{"key":"details_layout","module":"JOB",…}}` (it saves the
  viewer's layout preference). A read-only script must block it.

**New job** — `/jobs/new`: Job Title*, Prefix, Job Value, Due Date, Start / End date & time
("Check Availability"), Service Address*, Billing Address*, Trade Type, Job Category*, Priority,
Job Type, Parent Job, Tags, Lead Source, Description; sections for Tasks, Line Items (pricelist),
Assign Users, Customer, Secondary Contacts, Project, Property, Attachments.

### 3.2 Projects — `/projects`

Columns: Project Number, Name, Customer, Category, Address, Status, Priority, Project Date,
Project Manager, Users Assigned, Due Date, Created On. List or Kanban. Bulk: Update Field,
Update Status, Assign / Unassign, Delete. Two categories exist: *Roofing* and *AHS - TEST*, and
project custom fields hold the AHS test's seven field groups. New project adds Project Schedule,
Invoicing (Progressive Invoicing), Project Manager. API: `POST /api/projects/filter`,
`GET /api/projects/category`.

### 3.3 Photo Feed — `/photo_feed`

Every photo uploaded to jobs, grouped **By Date** or **By Job**, with Filter, date range,
Download, Add / Remove Tags, Delete. API: `GET /api/attachments/group?is_photo_feed…`,
`GET /api/attachments/photo_feed?filter.from_date,filter.to_date`. (The owner's rule: never touch
gallery photos.)

### 3.4 Calendar — `/calendar/week`

Views Day / Week / Month / Resource, saved views, a mini month picker, Event Types (Jobs,
Non-Job Events, Timeoffs), Teams and Users checklists, and an **Unscheduled Jobs** panel. "More
filters": Teams, Users, Category, Status, Assignment Status, Due Date, Customer, Property,
Priority, Skills, Service Territory, Tags, Projects. API: `GET /api/jobs/schedule?from_date,to_date,view` ·
`/api/schedule/calendar/count` · `/api/non_job_events` · `/api/timesheets/request/timeoff` ·
`/api/company/holidays`.

### 3.5 Dispatch Board — `/dispatch_board`

Two tabs. **Jobs:** an unassigned-jobs list beside a per-user timeline (Create Job, Refresh,
Filters, Settings, zoom, Today). **Routes** (`?tab=ROUTES`): a map with route name, colour,
start address, duration, layers. API: `POST /api/jobs/dispatch_board/filter` ·
`POST /api/jobs/employee/schedule/filter` · `GET /api/teams/summary` · `/api/territory` ·
`/api/location/nearby` · `POST /api/routes/filter`. Dragging a card here reschedules or reassigns
a job — never do it from a script.

### 3.6 Maps — `/maps`

Tabs Users (live location, History), Jobs, Routes (Edit Route, Clone Route), Properties,
Customers. API: `GET /api/location/users` · `/api/routes?from_date,to_date` · `/api/jobs` ·
`/api/property` · `/api/customers`.

### 3.7 Customers and Properties — `/customers`, `/property`

- **Customers columns:** Name, Email, No of Jobs, Category, AR Balance, Work / Mobile / Home No,
  Tags, Status, Created On. Bulk: Update Field, **Email / Text Message**, More actions, Export.
  Filter fields include every name / phone / address part, Has card on file, Jobs Count, AR
  Balance, Lead Source, and the customer custom fields (Lead, Roofle ids, Market).
- **Customer page** `/customers/<uid>/details?tab=OVERVIEW`: Mail, Call, Add Note, Notification
  Preferences; Primary Details; tabs Overview, Jobs, Quotes, Invoices, Projects, Communication,
  "+5 More"; Highlights (Receivables, Credits, Last Email), Recent Activity / Jobs / Quotes /
  Invoices / Payments / Projects, Attachments, credits (View Credit History, Add Credits).
- **New customer:** First Name*, Email* (email is mandatory on this account), Company, Category,
  Tags, Has SLA, Pricelist, Lead Source, phones, tax, contact person, Service Address*, Billing
  Address, and the custom fields. A property is created automatically with a new customer.
- **Properties columns:** Property Name, Jobs Count, Customer, Status, Tags, Created On.
- API: `POST /api/v2/customers/filter` · `GET /api/customers/<uid>` · `/api/customers/<uid>/summary` ·
  `/api/outbound_logs` · `POST /api/property/filter`.

### 3.8 Quotes, Invoices, Payments, Commissions

- **Quotes** `/estimates`: Quote No., Title, Quote / Expiry / Accepted / Converted Date, Customer,
  Job, Total, Status. Bulk: Update Field, Update Status, **Send Email to Customer**, **Send Text
  Message**. Filters include Is Proposal, Pending Signature From, Deposit Status, Sold By.
  New quote has **Save as Draft** and **Save & Send** side by side.
- **Invoices** `/invoices`: Invoice No., dates, Customer, Job, Contract, Total, Status. Bulk:
  Update Field, Update Status, **Charge Card**. **AR Management**: Overview and AR Aging Summary.
- **Payments** `/payments/dashboard`: Dashboard, Payments (`/payments/transactions`), Payouts,
  Reports (Payment Summary, Detail, Method, Refund, Chargeback, Payout Reconciliation).
- **Commissions** `/commissions`: Commission Date, User, Job, Project, Commission Amount, Payout
  Amount, Payout Status; Payout, Mark as Paid / Unpaid.
- API: `POST /api/estimate/filter` · `POST /api/invoice/filter` · `GET /api/analytics/ar_aging` ·
  `GET /api/analytics/payments/stats|trends` · `POST /api/payments/transactions/filter` ·
  `POST /api/commissions/filter` · `GET /api/invoice_estimate/template`.

### 3.9 Production and Catalog

- **Material Requests / Material Orders / Suppliers / Transfer Orders:** all empty. Create forms:
  a Material Order needs a Supplier, title, Required By; a Material Request has delivery method
  ("Direct Shipment to Job's site"), priority, parts, and a job / quote link.
- **Parts & Services** `/products`: 1,723 items. Columns ID, Name, Part / Service No, Type,
  Category, Brand, Specification, Quantity, Unit Price. Bulk: Update Field, Print QR Codes,
  Connect to Supplier. New item: billable / profitability / commissionable flags, markup,
  options, quantity formula, serial tracking, suppliers, location availability.
- **Item Groups** `/product_groups`, **Pricelists** `/pricelists` (1).
- API: `POST /api/product/filter` · `/api/product/group/filter` · `/api/products/pricelist/filter` ·
  `/api/material_requests/filter` · `/api/purchase_orders/filter` · `/api/vendors/filter` ·
  `/api/products/transfer_orders/filter`.

### 3.10 Timesheets — off

`/timesheets` (New Entry, Defaulters, More Actions: Timesheet Approval, Manage Locations, Manage
Enrollments), Summary View `/timesheets/grid`, Timeoff Requests, Shift Management `/shifts`.
"Enable Timesheet?" is **No** in Settings.

### 3.11 Reports

- **Reports** `/reports`: 78 built-in reports in 9 groups (Job, Quote & Invoice, Product, User and
  Team, Customer, Project, Property, Production, Miscellaneous). Each is
  `/reports/default/<module>/<name>` with a date range and **Generate Report** (which queues an
  export; see Report History `/reports/reports_history`, and Scheduled Reports).
  Job reports: master, status, status_summary, status_history, status_details, deviation, notes,
  avg_duration_category, products, products_detail, customer_cohort, feedback, **checklist**,
  employee_utilisation, product_consumption, activity, profitability, recurrence, service_task,
  team, timelog, expenses. Others cover estimate, invoice, proposal, customer, employee,
  commission, product, projects, property, purchase_orders, material_request, routes,
  timesheet, vendor, non_job_event, service_task.
- **Advanced Reports** `/advanced_reports/all`: All / Starred / Mine / Shared, Templates,
  Scheduled, Report History, folders. API under `/api/reports-service/v3/…`.
- Dashboard `/dashboard`: widgets filtered by date, Trade Type, Category, Team; "Customize"
  adds widgets. API: `GET /api/analytics/job_stats/*`, `/api/analytics/estimate|invoice|user_stats`.

### 3.12 Communication — `/connect/conversations`

Two tabs: **Conversations** (search by customer or number, Open filter) and **All Emails**
(`/connect/emails`, "No mailbox connected"). The crawl blocked every request to the Connect
host, so conversation lists were not loaded. `/chats` is the internal team chat (Everything /
Unread / Recent).

### 3.13 Workflows — `/workflows`

The page is a shell that loads the workflow list from the workflow host
(`GET us-east-1-workflow.zuperpro.com/api/workflows?filter.is_active…`,
`/api/need_attention/workflows`); it rendered nothing in the headless browser. Settings →
Miscellaneous → Workflows loads the old app (`app.zuperpro.com`) and also showed nothing.

---

## 4. Settings — `/settings_new/...`

Left menu groups: **Work** (Projects, Jobs) · **CRM** (Customers/Contacts, Properties) ·
**People & Access** (Timesheets, Security, Users & Teams) · **Catalog** (Parts & Services,
Production) · **Accounting** (Quotes & Invoices, Zuper Pay) · **Data & Automation** (Data
Administration, Miscellaneous, Developer Hub, App Store) · **Customer Channels** (Communication,
Customer Experience) · **Zuper Labs**. Almost every page reads `GET /api/company/config` and its
Save writes the same object back.

### 4.1 Jobs

| Page | Route | What it holds |
|---|---|---|
| General Settings | `job/configuration` | tabs General, Access/Permissions, Assignment, Route, Chat (below) |
| **Job Category Hub** | `job/category` | 19 categories; a category page (`job/category/<uid>/details`) has tabs **Job Status** (name, Status Type, Checklist; New Job Status, Import From Other Category), **Tasks**, **Skillset** |
| Job Card Templates | `job/job-card-templates` | 7 templates |
| Document Templates | `job/documents` | e-signature documents; none |
| Job Notifications | `job/notifications` | Internal (reminders, delay alerts, status alerts) and **Customer/Contact** (reminders, status alerts) |
| Job Custom Fields | `custom_fields/jobs` | 17 field groups (below) |
| Skillsets | `job/skills` | 9 |
| Job Costing & Expenses | `job/job-costing` | General (profit method Margin), Labor codes / types, Expense categories, Commission structures — all empty |
| Task Template | `job/service-task` | 50 templates |
| Non Job Event Categories | `job/non-job-events` | 5 |
| Inspection Forms | `job/inspection-form` | 16 |
| Measurements | `job/measurements` | tokens (11 categories) and providers |
| Gallery Settings | `job/gallery` | default albums: Adjuster, Build, Completion Photo, Crew, Homeowner, Inspection |
| Layout Customization | `job/layouts` | 1 layout |
| Appointment Types | `job/appointment-type` | none |

State read on 2026-10-05 (General): Recurring Jobs **off**, Secondary Contacts on, Job Gallery
on and **not public by default**, Job Value on, photo stamps (geo, date) on, Service Territories
off, Job Visibility for Technicians off, Kanban on, **"Allow sending public link to customer":
No**, Timelog off.
Access: a technician can create and edit jobs, cannot reschedule, cannot accept / decline.
Assignment: "Notify users upon assignment" **Yes**. Route: technicians cannot optimise routes;
miles.

**Customer/Contact status alerts: 21, every one In-Active**; no customer reminders. One internal
delay alert exists and is In-Active.

Job field groups: Insurance, Warranty, Trades, Source Attribution, Drip Campaign State (all
empty), **AHS** (6), **Checklist** (26), **CRM** (9), **KPI** (7), and seven "AHS TEST - …"
groups.

### 4.2 Other settings

| Area | Pages (route under `/settings_new/`) | Notes |
|---|---|---|
| Projects | `project/configuration`, `project/category`, `custom_fields/project` | timelog on, Kanban on |
| Customers/Contacts | `customer/configuration` (General, Access/Permissions), `customer/category` (3), `customer/lead-sources` (22, incl. AHS), `customer/billing-frequency`, `custom_fields/customer` | unique field = Email; **Portal access by default: No**; auto-create property: Yes |
| Properties | `property/configuration`, `custom_fields/property` | |
| Timesheets | `timesheet/configuration`, `timesheet/approval`, `timesheet/master-shifts` (2), `timesheet/timeoff-request` | timesheet off |
| Security | `security/password`, `security/login-logs` | Google sign-in off; lockout rule off |
| Users & Teams | `users-and-teams/configuration`, `/users`, `/teams`, `/custom-roles`, `custom_fields/employee|team` | location tracked via Timesheet |
| Parts & Services | `products/configuration`, `products/category` (73), `products/locations` (1 warehouse), `products/suppliers`, `custom_fields/products` | markup on, options on |
| Production | `purchasing/configuration`, `purchasing/material-requests-templates`, `purchasing/purchase-order-templates` (2), `purchasing/suppliers` | |
| Quotes & Invoices | `quotes-and-invoices/configuration` (General, Quote, Invoice, Financing), `/templates` (6), `/proposal-layouts` (4; **DTR Proposal** is the active one), `/proposal-templates` (Zuper IQ: 21 templates, Formula Library, Service Packages, Financing Options), `/tax` (7 sample WA taxes), `/discounts`, `/payment-modes-terms` (5 modes, 6 terms), `/reminders` (3), `/packages` (5), `custom_fields/estimates|invoices` | see below |
| Zuper Pay | `zuper-pay`, `/payment-methods`, `/surcharge-settings`, `/terminal-management`, `/location-management`, `/account-management` | not onboarded |
| Data Administration | `data-admin/import` (Import, Past Imports, Saved Templates), `data-admin/migration`, `data-admin/recovery` | |
| Miscellaneous | `misc/business-units` (Trade Types, 10), `misc/tags` (133), `misc/email-templates` (**828** Email/SMS templates), `misc/email-settings` (SMTP; none), `misc/holidays`, `misc/service-territory` (5 sample), `misc/approval-hierarchy`, `misc/broadcast`, `misc/custom-dashboards`, `misc/pdf-library`, `misc/sos-settings`, `misc/workflows` | |
| Developer Hub | `developer/api-keys`, `developer/webhooks`, `developer/custom-functions` (Your Functions, Execution History), `developer/sync-history` | |
| Communication | `communications/phone-numbers` (Phone Numbers, User Permissions), `/call-settings` (General, Call Groups, Call Routings, Call Tags), `/message-settings`, `/notification-settings`, `/ai-responder` (CSR Agents), `communication/email` (Manage Inboxes, 1 not connected) | Connect data not loaded (host blocked) |
| Customer Experience | `customer-widget` | no widgets |
| Zuper Labs | `labs` | 1 experiment, off |

Quote settings read on 2026-10-05: customers may accept a quote and must sign; default layout
DTR Proposal, presentation "Proposal PDF"; **attach quote PDF to email: No**; **public link to
customers: Yes**; deposit on quote enabled but not mandatory; add-ons on.
Invoice settings: default template "Default Invoice Template"; **public link: No**; payment
collection Yes via Zuper Pay; automatic payments No; tipping No; auto-convert quote to invoice No.
Financing: off. Follow-up reminders exist (3: "NET 15" email on quotes, an email and a text on
invoices) — the page does not show whether they are active; check before sending any quote.

---

## 5. API behind the UI

Base `https://us-east-1.zuperpro.com/api` (same as the public API, same data). Other hosts the
app talks to: `us-east-1-workflow.zuperpro.com` (workflows), `us-east-1-connect.zuperpro.com`
(Zuper Connect telephony), `apps.zuperpro.com` (app installs, sync history),
`migration-api.zuperpro.com`, `accounts.zuperpro.com` (login), `p.zuperpro.com` (feature flags,
surveys), `chat.stream-io-api.com` (team chat, WebSocket).

Loaded on every page: `/user`, `/user/company`, `/user/preferences`, `/company/config`,
`/company/modules`, `/company/policy`, `/meta_data/all`, `/business_units`, `/layouts`,
`/mailbox`, `/labs/features`, `/broadcast/mine`, `/service_tasks/count`,
`/user_notifications/count`, `/messaging/message/unread`, `/payments/provider`.

Patterns worth knowing:

- **Lists are POSTs:** `POST /<module>/filter` with `{limit, page, filter_rules:[{key, operator,
  value, field_type, module, type}], filter_rule_operator, sort, sort_by}`. Modules seen: `jobs`,
  `projects`, `v2/customers`, `property`, `estimate`, `invoice`, `commissions`, `product`,
  `product/group`, `products/pricelist`, `products/transfer_orders`, `material_requests`,
  `purchase_orders`, `vendors`, `timesheets`, `routes`, `payments/transactions`,
  `jobs/dispatch_board`, `jobs/employee/schedule`.
- **What a list can filter and show:** `GET /<module>/meta` and `/<module>/meta/filter`.
- **Saved views:** `GET /misc/quick_filters?filter.module=…`.
- **Custom fields and their groups:** `GET /settings/custom_fields?module_name=…`,
  `GET /settings/custom_fields/group?filter.module_name=…`.
- **Settings lists:** `/jobs/category?populate_statuses`, `/jobs/reminder`, `/jobs/delay_alert`,
  `/jobs/status_alert`, `/customer_notification`, `/misc/email_template`, `/invoice_estimate/template`,
  `/invoice_estimate/proposal_template`, `/invoice_estimate/cpq/formulas`, `/invoice_estimate/package`,
  `/invoice_estimate/reminder`, `/invoice_estimate/tax`, `/layout_templates`, `/service_tasks/master`,
  `/skills`, `/territory`, `/teams/summary`, `/access_role`, `/company_tags`, `/settings/api_key`,
  `/service/notifications/webhook` (webhooks — note: no `/api` prefix), `/misc/custom_functions`,
  `/v3/imports/*`, `/booking/widgets`, `/ai/ai_agent`.
- **Sense:** `/sense/threads`, `/sense/suggestions`, `/sense/radar/radars`, `/agent-builder/skills`.

---

## 6. How to run the read-only browser again

On dispatch, as user `qa`, in `/home/qa/zbrowser/nextmap/` (0700):

| File | What it is |
|---|---|
| `../zuper_next_state.json` | the saved login for `next.zuperpro.com` (0600). The old `zuper_state.json` does **not** work on the new UI. |
| `zn_common.py` | the guarded browser (`open_next`, `new_page`, `go`) — use it for every script |
| `allow.json` | the POSTs allowed through: 17 list filters and the feature-flag call |
| `zn_crawl.py routes.json 0 1` | breadth: one card per route into `pages/` |
| `zn_depth.py` | depth: named tabs and read-only menus into `depth/` |
| `zn_digest.py`, `zn_digest2.py` | condense the cards |
| `blocked.log`, `allowed.log` | every request refused / every POST let through |
| `pages/`, `depth/`, `shots/` | raw results and screenshots — **real customer data, never copy off the server** |

What the guard does, and why each part exists:

1. **Browser timezone is America/New_York.** From any other zone the new UI opens "Your timezone
   has changed … **Update**", and that button rewrites the account's timezone.
2. **No WebSocket opens** and **every request to the Connect host or a phone vendor is refused**,
   so the browser can never register as a phone line. (The page asks Connect for an agent
   registration on every single load.)
3. PUT / PATCH / DELETE are refused. A POST passes only if its exact path is in `allow.json`
   and its URL has no action word (send, email, sms, message, notify, invite, dial, workflow,
   import, export, delete, pay, …).
4. Pictures and fonts are not loaded and one tab is used: dispatch has little free memory, and
   two browsers at once nearly exhausted it.
5. `zn_depth.py` clicks only controls named in its list and refuses any name with an action word
   (Save, Send, Delete, Update, Call, Enable, Connect, Create, Add, …).

Never call sync Playwright methods from inside an event handler (a route or `page` listener):
the first attempt hung for 40 minutes that way.

---

## 7. Not mapped, and why

| What | Why |
|---|---|
| Connect conversations, call history, phone numbers, call groups / routings / tags, message settings | every request to the Connect host was blocked on purpose; the new UI has no separate call-history page to allow |
| Payments → Payments list, Payouts | their reads contain "pay" in the URL, which the guard treats as an action word |
| Workflows list | the page rendered empty in the headless browser; read workflows through the API instead (operations doc, section 2) |
| The other "+6 More" tabs of a job page and "+5 More" of a customer page | the tabs are not standard tab elements; only the Overview tab was read |
| Job Category Hub → the status rows of a category | the table had not filled in when the card was taken; the same data is `GET /jobs/category?populate_statuses` |
| Timesheet settings tabs (Time-off, Approval) | the click timed out |
| Bulk-action menus on lists | they need a row ticked first |
| Sense (the AI chat) | nothing was typed anywhere |
| A technician's view | admin login only |
| Modules this account does not have | listed in section 1 |
