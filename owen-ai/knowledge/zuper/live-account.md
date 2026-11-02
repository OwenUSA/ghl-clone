# Zuper live account — Dream Team Roofing (state on 2026-11-02)

Read-only reconnaissance of the production Zuper account, done 2026-11-02 between 18:00 and 18:45 UTC.
Every request was a GET. Nothing was created, changed, sent or triggered. The raw JSON is on
**owen-main** in `/root/owen-ai-recon/` (re-read with `jq`; GET helper `zget.sh <path> <outfile>`,
which reads the key from `/opt/santiagoproperties/ghl-clone/.env.prod` and never prints it).
For endpoint semantics and confidence markers, see `api-reference.md`. **This file records what the account
actually contains.**

Privacy: real customers appear here only by job number (`#757`) or proposal number (`DTR-58`).
Only the test customer's details are written out.

Base URL `https://us-east-1.zuperpro.com/api`, header `x-api-key`. The key belongs to Owen
Buzaglo's user, so any write shows in Zuper as made by Owen.

---

## 0. Raw files on owen-main (`/root/owen-ai-recon/`)

| File | What |
|---|---|
| `ptemplates_all.json` / `dtr_templates.json` | all 21 proposal templates / the 4 DTR ones |
| `templates.json` | 6 quote/invoice PDF templates |
| `categories.json` | 18 job categories (boards) |
| `st_simple.json`, `st_insp.json` | statuses + checklists of AHS Simple Flow / AHS - Inspection |
| `estimates_all.json` | list rows of all 128 proposals (trimmed: options have no line items) |
| `est/<uid>.json`, `est_details.json` | full detail of every proposal (128) |
| `jobs_simple.json` | all 58 jobs on AHS Simple Flow |
| `jobs_cat_<uid>.json` | first 100 jobs of AHS - Inspection, AHS - Repair & Review, Retail, AHS - TEST |
| `cust_test.json`, `jobs_test.json`, `job_701.json` | test customer, their jobs, job #701 |
| `products_all.json` | the whole catalog (1,724 items) |
| `cf_estimate.json`, `cf_job.json` | custom-field definitions for ESTIMATE (12) and JOB (84) |
| `notes_*.json` | job-notes samples |
| `analyze.sh` | the jq analysis behind section 2 (re-runnable) |

---

## 1. Proposal templates (Zuper "Zuper IQ / CPQ" proposal templates)

**Endpoints (verified, GET):**
- List: `GET /invoice_estimate/proposal_template?count=50&page=1` returns `{data[], total_records, total_pages}`. The default page size is 10, so pass `count`.
- One: `GET /invoice_estimate/proposal_template/{template_uid}` (documented; the list already returns full records).
- Record keys: `template_uid, template_name, template_description, template_type ("CPQ"), cpq_config.trigger[], proposal_options[], option_groups[], is_active, is_draft, created_by, updated_at`.

There are 21 templates. 17 are Zuper samples imported 2026-09-10 (GAF, Owens Corning, CertainTeed, IKO,
"Multi Group", "Repair CPQ Test", "Do not edit", ...) and are **never used**. **The 4 DTR templates** were
created 2026-09-18 by Owen:

| # | Template | template_uid | Used (of 128) |
|---|---|---|---|
| 1 | DTR - Flat Roof Leak Repair (Good / Better / Best) | `784d8985-ef0d-4d49-ae08-113a8c9b7ff9` | 10 |
| 2 | DTR - Tile Leak Repair (Good / Better / Best) | `eb322d43-0d4b-4d36-93de-2ac528a8d25e` | **100** |
| 3 | DTR - Roof Replacement (Good / Better / Best) | `06ce5f36-651f-4009-b34c-1194a06cb8c8` | 1 |
| 4 | DTR - Custom (Good / Better / Best) | `7c697c78-8760-4049-93b8-9a4050c9eeab` | 7 |
| — | (no template, plain quote) | — | 10 |

Shared structure:
- Three options, `GOOD`, `BETTER`, `BEST`. **BEST is `is_recommended: true`**.
- Each option has exactly one `ITEM` line (qty 1, `quantity_type: FIXED`, `section_type: EXPANDED`) pointing at a
  **DTR Packages** product (template lines reference the product by its Mongo `_id` in `product`).
- Option discount is `{type: FIXED, discount_applicability: TRANSACTION}` with no value. Financing is off. There are no add-ons,
  no option groups, no formulas or pricing rules, no option images.
- `cpq_config.trigger` on all 4 is **AHS - Inspection / Proposal Made** (`f07e3c01…` / `7cac884d-7206-498a-946d-4fe982cf0017`).
  Moving a job on AHS - Inspection into "Proposal Made" can therefore make Zuper auto-generate a
  proposal from these templates. Owen tested this on 2026-11-02 (test proposals DTR-140…143, `cpq_status.proposal_generation_type: "MERGE"`).
  **AHS Simple Flow has no trigger.** Its "Proposal Creation" stage is not wired to any template.
- Prices and descriptions come from the product. A new proposal **copies the product's HTML description**
  (`<p><strong>Scope of Work</strong></p><ul><li>…</li></ul><p>Workmanship Warranty…</p><p>Payment terms…</p>`).

Per template:

| Template | GOOD | BETTER | BEST |
|---|---|---|---|
| Flat Roof Leak Repair | `DTR-FLAT-GOOD` "GOOD - Essential Flat Roof Leak Repair" $1,500 · "Essential repair - fixes the active leak." | `DTR-FLAT-BETTER` "BETTER - Heavy-Duty Flat Roof Leak Repair with Double Underlayment" $1,900 | `DTR-FLAT-BEST` "BEST - Maximum Protection Flat Roof Leak Repair + Ponding Area Fix" $3,000, plus a **LOOKUP** line "Repair items needed" (added 2026-11-02, see below) |
| Tile Leak Repair ("Good and Better are written on site") | `DTR-CUSTOM-GOOD` "GOOD - Custom Package (write scope and price on site)" $0 | `DTR-CUSTOM-BETTER` $0 | `DTR-TILE-BEST` "BEST - Maximum Protection Tile Leak Repair" $2,000 |
| Roof Replacement ("Good and Best are written on site") | `DTR-CUSTOM-GOOD` $0 | `DTR-SHINGLE-RR-BETTER` "BETTER - Complete Roof Replacement \| Architectural Shingles" $5,200 | `DTR-CUSTOM-BEST` $0 |
| Custom (blank) | `DTR-CUSTOM-GOOD` $0 | `DTR-CUSTOM-BETTER` $0 | `DTR-CUSTOM-BEST` $0 |

Option uids (template-level, regenerated on each proposal):
Flat 053ac574… / 4c8d2472… / 2e25242f…; Tile 46305e5c… / cb0c866b… / 36c3e6f0…;
Replacement aa56af4a… / d0b11eae… / 2b548bf2…; Custom 8db13889… / 54994008… / 9b26dc77… (full uids in `dtr_templates.json`).

**LOOKUP line (new, 2026-11-02, Flat template BEST option):** `line_item_type: "LOOKUP"`,
`lookup_field_type: "CHECKLIST"`, `field_uid: 6ea93f99-3192-43f4-a0e7-25597a946880` = the checklist question
**"Repair items needed"** (type LOOKUP, a product picker) on **AHS - Inspection / Inspection Completed**
(`853740e0-10e6-4978-a498-7a818ea38207`). When CPQ generates the proposal, the products the technician picked there are
merged in as lines. In DTR-142, catalog items like `ROOF-LBR-001` were merged into GOOD. Owen is building this now, so treat it as experimental.

**PDF layout:** every proposal uses layout **"DTR Proposal"** `e9b8add9-8fb8-44a4-9406-d8b9b7bec422`
(`layout_association.layout_uid`). Quote template (`estimate.template`): **"Quote Template"**
`d979180c-8a39-4bfc-802f-2cbcc20d64eb` (122 of 128). The other 6 use "Default Quote Template" `619fa472-300f-4152-a513-3f3f2005cdcf`.
Other PDF templates (`GET /invoice_estimate/template`): Invoice `57862374-…`, Proposal Template `9bc701c9-…`,
Commercial Consolidated Quote `7ec4206b-…`, Default Invoice Template `8e3fa26b-…`.

---

## 2. How proposals have actually been made (all 128, 2026-09-24 → 2026-11-02)

**Endpoints:**
- List: `GET /estimate?count=50&page=N&sort=DESC&sort_by=created_at`. There are 128 in total. **The list trims options**
  (only `option_uid, option_name, is_accepted`; no line items or totals).
- Detail: `GET /estimate/{estimate_uid}` returns the full record (options with `line_items`, `attachments`,
  `status_history`, `signatures`, `layout_association`, `proposal_template`, `public_url`, ...).
- Filter search (documented, POST, not exercised): `POST /estimate/filter`.

### Who, when, status
- Created by **Luis Candiales (Dispatcher, 120)** and Owen (8, all tests). `sold_by_user`: Luis 77, Owen 6, Antonio 3, none 42.
- Status: APPROVED 80, AWAIT_RESPONSE 25, CANCELED 11, DRAFT 10, AWAIT_SIGNATURE 1, ARCHIVED 1.
- Lifecycle (`status_history[].status_name/done_by_type`): the usual path (73 of 128) is
  `DRAFT/EMPLOYEE → AWAIT_RESPONSE/EMPLOYEE (sent) → AWAIT_SIGNATURE/CUSTOMER → APPROVED/CUSTOMER`.
  Customers accept and sign online through the public link. 75 were then **converted to an invoice** (`is_converted`).
- Linked job's board: AHS - Repair & Review 81, **AHS Simple Flow 27**, Retail 8, AHS - Inspection 7, Leak repair 1, no job 4.
  Every proposal has `customer` and almost all have `job`. `property` is usually null.
- Number: `prefix: "DTR-"` + `estimate_no` (DTR-11 … DTR-143). Expiry is estimate_date + 30/31 days (Zuper's default).

### Options actually sent

| Option set | Count |
|---|---|
| GOOD only | **59** (32 of them $0) |
| GOOD, BETTER, BEST | 36 |
| GOOD, BETTER, BEST + an "ADD-ON(S)" / "ADD-ON UPGRADES" 4th option | 6 |
| BETTER only | 11 |
| other (BEST only, GOOD+BETTER, renamed options, "Recovery Roof / New Roof", "DUAL LEAK REPAIR", ...) | 16 |

- Recommended is always BEST (42) when there are three options.
- Accepted: GOOD 62, BETTER 12, BEST 8 (plus 4 renamed). **Most AHS customers take the $0 / low GOOD.**
- A 4th "add-on" is made as an **extra option** (Zuper's `addons[]` is used once in total).

### Line items (product code → uses across all options)

| Code | Name | Uses | Unit price range | Accepted |
|---|---|---|---|---|
| `DTR-CUSTOM-GOOD` | GOOD - Custom Package (write scope and price on site) | 159 | $0 – $3,490 | 62 |
| `DTR-CUSTOM-BETTER` | BETTER - Custom Package | 57 | $0 – $4,985 | 11 |
| `DTR-TILE-BEST` | BEST - Maximum Protection Tile Leak Repair | 33 | $1,500 – $7,970 | 4 |
| `DTR-FLAT-BEST` | BEST - Max Protection Flat Roof Leak Repair + Ponding | 14 | $1,200 – $3,200 | 4 |
| `DTR-FLAT-GOOD` | GOOD - Essential Flat Roof Leak Repair | 12 | $0 – $1,500 | 2 |
| `DTR-FLAT-BETTER` | BETTER - Heavy-Duty Flat Roof Leak Repair | 10 | $650 – $1,900 | 0 |
| `DTR-CUSTOM-BEST` | BEST - Custom Package | 6 | $1,500 – $4,175 | 0 |
| `L024` Plywood Replacement (Per Sheet) $500 · `UPG-GOOD/BETTER/BEST` · `AHS-LEAK-REPAIR` · `DTR-SHINGLE-RR-BETTER` · `DTR-BALCONY-SHINGLE` · `GUTR-LBR-002` · ad-hoc "GOOD OPTION" lines (`737/738/739`) | rare | | |

- **Quantity is always 1.** Every option is one package line, and the price is the option price. The line name stays the product
  name ("GOOD - Custom Package (write scope and price on site)"). The real title of the option is written in
  **`option_description`**, e.g. "Targeted AHS Primary Covered Leak Repair Package",
  "Plywood Decking & Better Underlayment", "Double Underlayment, Plywood Decking Replacement & 2x Expanded Area
  Waterproofing Package", "Basic Valley Underlayment Leak Repair Package (2-Story)".
- **Taxes: none** (0 of 128 with tax; one tax-exempt). **Discounts: none** at estimate or option level. Fees: 1.
  Any "Dream Team Discount" is written as text in the description, not as a Zuper discount.
- **Deposit: 50%.** Each option's `deposit` = 50% of its total (50 of 59 with deposit >0; 8 at 100%).
- Custom fields on ESTIMATE (12 defined: Financing "Option N: 120mo./180mo.", Shingle Color, QBO Class, Customer
  Type, Test) are **never filled** (0 of 128).
- Tags: rarely used ("AHS", "Shingle roof").

### The description convention (most important for drafting)
Luis rewrites the line's description for each job. The structure is the same every time (HTML in `description`; Zuper
also returns `markdown_description`):

```
**Scope of Work**
* (Retail Value: $X | Special AHS Member Price: $Y | AHS Authorized Credit: -$1,125 | Customer Pays Out of Pocket: $Z | Total Savings: $S)
* <Step title>: <what we do> ...            (5-8 bullets: diagnostic/prep, removal, underlayment, plywood,
* ...                                          flashing/penetrations, reinstall/re-integrate, cleanup & final inspection)
Workmanship Warranty: <30 days | 90 days/3 months | 6 months | 1 year> This warranty covers workmanship on <...> for <...> from completion.
Payment Terms: 50% Upfront ($Z/2) – due upon acceptance of proposal. 50% Upon Completion ($Z/2) – due after final inspection and customer approval. (AHS portion billed directly to insurance).
```

- The AHS credit is usually **-$1,125** (the AHS leak-repair authorization; the catalog `AHS-LEAK-REPAIR` is $1,100). Sometimes it is
  -$1,100 or a job-specific amount (job field `AHS authorized ($)`).
- Customer price = option total = "Customer Pays Out of Pocket". GOOD is often **$0** ("AHS covered").
- Warranty grows with the tier: GOOD 30 days–3 months, BETTER 3–6 months, BEST 1 year.
- Tiers build on each other: "Everything in Good, plus …", "Everything in Better, plus …".
- Wording varies a little between proposals (bold markers, "AHS Authorized" vs "AHS Authorized Credit", "Dream Team Discount" line).

### Titles (`proposal_title`) — conventions seen
| Pattern | Count | Era |
|---|---|---|
| `Active Leak Repair - <street address>` | 51 | most common, Oct 14 onward |
| `Proposal for <CUSTOMER NAME>` | 23 | Zuper default, early Oct and CPQ-generated |
| `<Customer Name> - <8-digit AHS dispatch #> ROOF` | 22 | AHS jobs, Oct 12 onward (matches the AHS work-order naming) |
| `Roof Repair - <address>`, `Inspection - Estimate - <address>`, `Tile Roof Leak Repair Options - <address>`, `New Gutters - <address>` | ~18 | retail / misc |

### Attachments
120 of 128 have 1–2 files: **`Proposal-DTR-<N>.pdf`** (74; built and attached to DRAFTs by the `zuper_proposals`
container on owen-main: a side-by-side options page with no signature page) and **`<AHS dispatch #> <LASTNAME>.pdf`**
(~80; the AHS work order PDF). No photos are attached to proposals; photos live on the job.

### Signatures
`signatures[]`: one CUSTOMER and one INTERNAL (the creator, `internal_user_type: CREATED_USER`) taken from the layout's SIGNATURE page.

### Sending (for reference only; never do it without the owner)
`POST /estimate/{uid}/send?send_proposal_layout=true` with `template_uid` + `email`… (a customer-facing send) and
`PUT /estimate/{uid}/status {"estimate_status":"AWAIT_RESPONSE"}`. Without `email` the same POST returns the PDF bytes.
The repo's `client.py` DENYLIST blocks all writes to `/estimate`, so an agent that creates proposals needs its own explicit scope.

### Creating a proposal — request body (NOT executed)

`POST /estimate` (docs: `POST /estimate/`). Required: `proposal_title`, `proposal_template`, `is_proposal`,
`proposal_options[].option_name`. This body shape was **proven live on 2026-09-24** by
`/home/qa/zbrowser/test_proposal11.py` on dispatch (DTR-11). It lands as **DRAFT** and sends nothing.

```json
{
  "estimate": {
    "is_proposal": true,
    "proposal_title": "Active Leak Repair - TEST - 100 Test St",
    "proposal_template": "eb322d43-0d4b-4d36-93de-2ac528a8d25e",
    "template": "d979180c-8a39-4bfc-802f-2cbcc20d64eb",
    "layout_template_uid": "e9b8add9-8fb8-44a4-9406-d8b9b7bec422",
    "prefix": "DTR-",
    "estimate_date": "2026-11-03",
    "expiry_date": "2026-12-03",
    "customer": "2dbd7187-a6e8-4452-8600-c3d029599d75",
    "job": "6abd527f-31a6-43e9-bb0a-6fe557dac596",
    "property": "c95aec5d-f44a-46b8-bfeb-30fd4d455d37",
    "customer_billing_address": { "street": "TEST - 100 Test St", "city": "Bradenton", "state": "FL", "zip_code": "34205", "country": "USA" },
    "customer_service_address": { "street": "TEST - 100 Test St", "city": "Bradenton", "state": "FL", "zip_code": "34205", "country": "USA" },
    "remarks": "",
    "custom_fields": [],
    "tax": [], "tax_exempt": false,
    "cover_image": null,
    "signatures": "<copy from GET /layout_templates/e9b8add9-…: SIGNATURE page's signatures; set the INTERNAL/CREATED_USER one to the creator>",
    "line_items": [],
    "proposal_options": [
      {
        "option_name": "GOOD",
        "option_description": "Targeted AHS Primary Covered Leak Repair Package",
        "is_recommended": false,
        "discount": { "type": "FIXED", "discount_applicability": "TRANSACTION" },
        "financing": { "is_enabled": false },
        "addons": [],
        "tax": [],
        "deposit": 0,
        "line_items": [
          {
            "line_item_type": "ITEM",
            "product_uid": "27e6e30d-ca23-4f92-a61b-f1f67a5402f5",
            "product_id": "DTR-CUSTOM-GOOD",
            "name": "GOOD - Custom Package (write scope and price on site)",
            "description": "<p><strong>Scope of Work</strong></p><ul><li>(Retail Value: $2,500 | Special AHS Member Price: $1,125 | AHS Authorized Credit: -$1,125 | Customer Pays Out of Pocket: $0 | Total Savings: $2,500)</li><li>…</li></ul><p>Workmanship Warranty: 3 Months …</p><p>Payment Terms: 50% Upfront ($0.00) – …</p>",
            "product_type": "SERVICE", "uom": "Job",
            "quantity": 1, "unit_price": 0, "total": 0,
            "discount": 0, "discount_type": "FIXED",
            "section_type": "EXPANDED",
            "tax": { "tax_exempt": false }
          }
        ]
      },
      { "option_name": "BETTER", "...": "same shape, DTR-CUSTOM-BETTER 38af9766-a840-43a8-a865-e18b5ecd18b3, deposit = total*0.5" },
      { "option_name": "BEST", "is_recommended": true, "...": "DTR-TILE-BEST ac78d138-ab66-4781-814d-9e95662318b4" }
    ]
  }
}
```

The response is `{type, message, data: {estimate_uid}}`. Re-read it with `GET /estimate/{uid}`. Notes:
- Edit later: `PUT /estimate/{uid} {"estimate":{estimate_uid,is_proposal:true,proposal_title,proposal_options:[…]}}` (repo-verified).
- When copying lines from an existing proposal, drop `line_item_uid` and `_id` (the script does).
- Keep descriptions as HTML (`<p>`, `<ul><li>`). Zuper's editor shows plain text badly.
- `zuper_proposals` will attach `Proposal-DTR-N.pdf` to any DRAFT about 1–2 minutes later. This is expected.
- Alternative: the CPQ route (move an AHS - Inspection job to "Proposal Made") auto-generates from the templates. It is a status move, which needs the owner's OK.

---

## 3. "AHS Simple Flow" — the new pipeline

Job category **AHS Simple Flow** `2fbc07ec-94e0-43ca-98d5-c902f12b59e5`, color `#E11D48`, created by Owen
**2026-10-31 17:52 UTC** (last edited 2026-11-01). It is 3rd in display order. Repo docs do not mention it yet.
Statuses: `GET /jobs/status/2fbc07ec-94e0-43ca-98d5-c902f12b59e5` (the response is `data.job_statuses[]`, each with
`checklist[]`). Jobs: `GET /jobs?count=100&page=1&filter.category=2fbc07ec-…`.

**7 stages, in order, with jobs per stage on 2026-11-02 (58 jobs total):**

| # | Stage | status_uid | Type | Jobs | Checklist (pop-up, none required) |
|---|---|---|---|---|---|
| 1 | Got a job from AHS | `c587fb9b-f637-4391-b85e-12dd6d048d03` | NEW | 17 | 15 intake questions: what is happening, leaks & location, roof age, previous repairs, roof material, stories, does customer live there, when leak started, first time using AHS, service address confirmed, second phone, email, preferred contact method, appointment day/time (DATETIME), header "Welcoming Call Pitch" |
| 2 | Day of Inspection | `b1255be9-7d4b-42a4-b5fe-fda80259bb34` | SCHEDULED | 0 | 15: scope / materials & tools / urgency / recommendation recorded, Album 1 360° house photos, Album 2 360° roof photos, Album 3 leak-location photos, roof detail photos, photo of every failure point (MULTI_IMAGE), 2–3 short videos (VIDEO), acute vs pre-existing reasoning, leak signs, visible failure, "Submit claim/authorization report to AHS in Frontdoor" |
| 3 | **Proposal Creation** | `cea9574b-8939-427c-8849-1a5898681e3e` | ESTIMATING | 5 | 6: price meeting (AHS-covered / non-covered / upgrade), **Good/Better/Best built with terms, prices, warranties, scope**, agreement sent, option chosen, document signed, **50% deposit paid** |
| 4 | Handoff Meeting | `4492c949-deac-41d2-8366-f00a9f3d2420` | SCHEDULED | 11 | 7: photos/scope/estimate match, materials on hand, Antonio briefed, crew/date/time, homeowner told window "from GHL", change orders approved by Owen first, same-day dry-in plan |
| 5 | Repair Day | `ea189583-9cca-4c10-9ab6-7a4e8e7a4c05` | STARTED | 9 | 15: scope confirmed, before/during/after photos, notes read, tools/materials on truck, **customer sign-off (SIGNATURE)**, walkthrough, tile quality checks (flat, interlocked, no lifted corners, no exposed nails, cracked tiles replaced, never open more than closed same day) |
| 6 | Quality Call | `a4906cd6-4571-4a84-b8b7-a709c0143aa4` | FOLLOW_UP | 2 | 8: repair good / still leaking / working / happy; if happy send Google review link; Antonio asks on site; if not happy no review push; note issue/owner/date |
| 7 | Job Completed | `d2caa95b-0f1d-4c1b-86ce-d16507bc6801` | COMPLETED | 14 | 8: invoice matches scope, AHS invoice separate from customer invoice, expenses, job costing within 48 h, tech commission, payment / AHS payment tracked, warranties sent, 6-month reactivation tag |

Almost all checklist items are `SINGLE_LINE` text used as a checkbox SOP. None are linked to job fields, and none are required.

**How it differs from the older AHS boards:**
- It replaces **two boards**, AHS - Inspection (18 columns) and AHS - Repair & Review (16), with **one 7-stage board**
  covering the whole lifecycle from intake through inspection, proposal, handoff, repair, quality call and completion.
- **No side columns.** There is no Cancelled, Reschedule Required, Waiting for Customer, Estimate Declined, Not Answering,
  On the Way, Submit to AHS, Awaiting AHS Decision, AHS Approved, Invoice Submitted to AHS or Paid. The AHS approval
  is a checklist line inside "Day of Inspection" ("Submit claim… in Frontdoor").
- **No KPI checklist questions** (Customer chose, Customer paid, AHS authorized ($), Cancel reason). Note that `kpi_report.py` reads those.
  Simple Flow jobs carry them only from their old board's history (5–8 jobs).
- No CPQ trigger: proposal templates fire only on AHS - Inspection / Proposal Made.
- The customer-text workflows (welcome, reminders, column texts) are bound to the old AHS boards and their column names,
  so presumably they do not fire for Simple Flow. Not verified.
- Dispatch, routes (`routes_sync.py` boards list) and the CRM mirror do not know this board yet.

**Migration in progress:** the board is 2 days old but holds jobs created as far back as September
(by creation month: Sep 24 jobs, Oct 29, Nov 5). The open AHS jobs were moved here from the old boards.
Current counts on the old boards: AHS - Inspection 30 (only 3 open: #756 Submit To AHS, #701 test, #413 Not
Answering; the rest are Cancelled 18 / Estimate Declined 9). AHS - Repair & Review 308 (in the first 100:
Paid 80, Awaiting AHS Payment 10, Callback* 8). Retail 101. AHS - TEST 0.
Also, AHS-email-created jobs (CRM `ahs_create`) still land on **AHS - Inspection** unless the code changes.

Simple Flow job line items seen: `AHS-LEAK-REPAIR` (23), `AHS-TILE-01` (10), `DTR-CUSTOM-GOOD` (7), `AHS-TRIP` (4),
`DTR-CUSTOM-BETTER` (3), `DTR-TILE-BEST` (2). Assignees: Owen 26, Antonio 18, Luis 13.
Filled job fields: CRM Opportunity ID / CRM Link (44), **AHS Job ID** (33, a new ungrouped field), the Checklist intake group (~24),
Technician (23), CompanyCam Project (15).

---

## 4. Test data (safe for write tests)

- Customer **Santiago Villahermosa (TEST)**, `customer_uid 2dbd7187-a6e8-4452-8600-c3d029599d75`,
  email santiagovillahermosa@gmail.com, address "TEST - 100 Test St, Bradenton FL 34205".
  **Mobile is now `+19549147244`.** The docs say +15618788090, so someone changed it. Confirm with the user that it is theirs
  before anything can text it.
- **Only one live job:** **#701** `6abd527f-31a6-43e9-bb0a-6fe557dac596` "TEST - AHS TEST pre-fill check",
  board **AHS - Inspection**, status **Proposal Made** (`7cac884d-…`), no assignee, property
  `c95aec5d-f44a-46b8-bfeb-30fd4d455d37`, job lines `DTR-TILE-BEST` $2,000 + `DTR-FLAT-GOOD` $1,500 (job_total 3,500),
  scheduled 2026-10-28. Notes: none.
  (The other test job #734 "Owen Test" belongs to a different customer.)
- Proposals on this customer:

| Proposal | uid | Status | Total | Job |
|---|---|---|---|---|
| DTR-143 | `6387d251-3bee-4da1-a554-989539fc8ad3` | APPROVED (converted) | 3,000 | #786 **deleted** |
| DTR-142 | `12cc37b5-19f3-4e50-9591-6a6874dc7771` | APPROVED | 3,000 | #785 deleted |
| DTR-141 | `a73ecbdb-5a56-44dc-ada6-304ada6b0571` | APPROVED | 3,000 | #784 deleted |
| DTR-140 | `3ccc064d-cfc8-4050-8606-6bcecf0e5a58` | AWAIT_RESPONSE | 3,000 | #774 deleted |
| DTR-139 | `2ed19bce-d22a-40c0-9daf-ee08fc142821` | APPROVED | 2,000 | **#701** |
| DTR-28 | `6df6087f-7886-4652-8a0b-afd16ada940e` | APPROVED | 1,500 | **#701** |
| DTR-13 / DTR-11 | `77da9a84-…` / `4052a4fc-…` | AWAIT_RESPONSE | 3,000 / 3,200 | #694 deleted |

  Owen created and deleted throwaway jobs #774/#784–#786 on Oct 31–Nov 2 while testing the CPQ trigger.
  Do not use a test job that is not on a real board; create a fresh one with the owner's OK or use #701.

---

## 5. Products / services catalog ("Parts & Services")

`GET /product?count=100&page=N` (18 pages). Record: `product_uid, product_id` (the code/SKU), `product_name,
product_category{category_uid,category_name}, product_type, price, purchase_price, uom, product_description, tax`.
Single product: `GET /product/{uid}`. Search (POST, documented): `POST /product/filter`.

**1,724 items**: PARTS 1,327, SERVICE 275, PRODUCT 92, BUNDLE 30. 1,589 are Zuper's 2026-09-10 sample import
(Other Fasteners 191, Roofing Materials 139, Siding 139, Flashing 132, Vents 125, Shingles 108, Gutter 83, ...,
"Roofing Services" with slope-priced tear-off/install lines). They are mostly not used by DTR.

**What DTR actually uses:**

| Category | Code | uid | Name | Price |
|---|---|---|---|---|
| DTR Packages (`0ed89503-6082-44bf-a98c-863dad72ee21`) | `DTR-FLAT-GOOD` | `aee8f00d-8abf-422d-99b3-79ee6b54a52e` | GOOD - Essential Flat Roof Leak Repair | 1,500 |
| | `DTR-FLAT-BETTER` | `7ea40183-bb9e-435e-88a5-d2b20d5b8b60` | BETTER - Heavy-Duty Flat Roof Leak Repair with Double Underlayment | 1,900 |
| | `DTR-FLAT-BEST` | `752843a3-f23d-4c31-8dec-a19560950580` | BEST - Maximum Protection Flat Roof Leak Repair + Ponding Area Fix | 3,000 |
| | `DTR-TILE-BEST` | `ac78d138-ab66-4781-814d-9e95662318b4` | BEST - Maximum Protection Tile Leak Repair | 2,000 |
| | `DTR-SHINGLE-RR-BETTER` | `36c5096e-7eac-4acf-b29d-9433ef2da6e4` | BETTER - Complete Roof Replacement \| Architectural Shingles | 5,200 |
| | `DTR-CUSTOM-GOOD` | `27e6e30d-ca23-4f92-a61b-f1f67a5402f5` | GOOD - Custom Package (write scope and price on site) | 0 |
| | `DTR-CUSTOM-BETTER` | `38af9766-a840-43a8-a865-e18b5ecd18b3` | BETTER - Custom Package (…) | 0 |
| | `DTR-CUSTOM-BEST` | `317c6a8a-7f64-4615-ad8a-b877a4bd5005` | BEST - Custom Package (…) | 0 |
| AHS (126 items) | `AHS-LEAK-REPAIR` | `cebd19ce-22fb-4462-988f-e8da7b4521a4` | AHS - Leak Repair | 1,100 |
| | `AHS-TRIP` | `bddef3dd-e230-4cf3-acb1-fb6e1807d009` | AHS - Trip / Diagnostic | 100 |
| | `AHS-ADDL-LEAK` | `559421cc-49ef-4f8f-9dfd-7138d7c6b9c4` | AHS - Additional Leak Repair | 0 |
| | `UPG-GOOD` / `UPG-BETTER` / `UPG-BEST` / `UPG-UNKNOWN` | `722b9eb5-…` / `e426f434-…` / `64cca2af-…` / `a8fdea5b-…` | Customer Upgrade - Good/Better/Best/(tier not recorded) | 0 |
| | `AHS-TILE-*`, `AHS-T*`, `AHS-S*`, `AHS-SHNG-*`, `AHS-F*`, `AHS-FLAT-*`, `AHS-FCOA-*`, `AHS-TVAL-*`, `AHS-SVAL-*`, `AHS-BTIL-*`, `AHS-PLYW-*`, `AHS-COMN-*`, `AHS-G*` | (see `products_all.json`) | $0 scope-step items (e.g. "Roof Inspection and Assessment", "Underlayment Installation", "Replacement Plywood"), 2026-09-28. Used as scope lines on AHS jobs (`AHS-TILE-01` on 10 Simple Flow jobs) | 0 |
| Roofing Materials | `Retail` (sic) | `89c725ad-d114-4bb3-942d-341aefd8fb2e` | Tiles Replace | 75 |

Also seen once each in proposals: `L024` Plywood Replacement (Per Sheet) $500, `GUTR-LBR-002` Aluminum Gutter Install,
`DTR-BALCONY-SHINGLE`, and sample `ROOF-LBR-001/002`, `ROOF-SHG-001` (merged by the CPQ lookup test).
KPI / commission logic keys on these codes (`kpi_report.py`: `AHS-LEAK-REPAIR`, `AHS-TRIP`, `AHS-ADDL-LEAK`, `UPG-*`,
`DTR-*` where the last word is the tier). New package lines should keep a `DTR-…-GOOD|BETTER|BEST` code.

---

## 6. Other things an agent needs

### Job notes
- `GET /jobs/{job_uid}/note` (verified) returns `{data: [ {note_uid, note_type "TEXT", created_by{…}, created_by_type,
  is_private, visible_to_customer, attachments[], user_mentions, is_v2_note, created_at} ]}`.
- **Open issue:** on the 3 real jobs sampled (#757, #739), the note records came back **without a `note` text field**,
  although the docs say `note` holds HTML. It might be how these notes were made (v1 vs v2), or a response trim. Check before
  relying on notes as context.
- Create (write): `POST /jobs/{uid}/note {"note":{"note":"<text>","is_private":true,"notify_users":false}}` (used by the CRM).
- A job's photos: `GET /attachments/folders?filter.module=JOB&filter.module_uid=<job_uid>` (albums), and photos on notes.

### Zuper Connect (texts and calls), host `https://us-east-1-connect.zuperpro.com/api/telephony`
Read endpoints the CRM uses (POST-shaped reads, verified 2026-10-19 by the repo, **not called in this recon**):
- `POST /message/conversations {"limit":25,"page":1,"filters":{"status":""}}` returns the inbox: `conversation_uid,
  is_unresponded, recent_conversation_at, recent_message, customer_number{customer_uid,…}`.
- `POST /messages {"filters":{"conversation_uid":…,"message_type":["SMS","MMS","CALL","SYSTEM"]},"limit","page":1}` returns
  a thread, newest first (`direction`, `message`, `module/module_uid` = the job, call recordings + summaries).
- `POST /calls {"page":1,"limit":100,"sort":"DESC","sortBy":"created_at","filters":{"current_user":false}}`, then
  `GET /calls/{call_uid}/details` for the call summary and next action.
- **Every other Connect endpoint can text or ring a customer. Never call one.**

### Custom fields
- Definitions: `GET /settings/custom_fields?module_name=JOB` (84) and `?module_name=ESTIMATE` (12). Both verified.
- Proposal-relevant JOB fields: `Customer chose` (AHS only/Good/Better/Best/Custom), `Option chosen`, `Sold price ($)`,
  `Customer paid ($)`, `AHS authorized ($)`, `AHS Approved Amount`, `Authorization Amount`, `Customer Responsibility`,
  `Estimate Amount`, `AHS Work Order #`, `AHS Authorization #`, `AHS Job ID`, the inspection inputs `Basic repair option` /
  `Expanded repair option` / `Comprehensive option` (= the technician's Good/Better/Best suggestions), `Probable cause
  identified`, `Leak location`, `Roof type`, `Roof material`, `Roof age (years)`, `How old is the roof?`, `How many leaks?`,
  `How many stories?`, `Possible add-ons`, `Tools & materials needed`, `Notes for the proposal`, `Couldn't inspect yet`,
  and the office checklist radios `Proposal created`, `Owen approved the proposal`, `Proposal shown and emailed to the
  customer`, `Deposit (50%) collected`.
- Reminder: `PUT /jobs {"job":{job_uid, custom_fields:[…]}}` **replaces the whole list**. Send every field, each with
  `label, value, group_name, group_uid, type`.

### Users that appear
Owen Buzaglo `74cd936c-9386-469b-addc-d05d3d1a06a0` (Admin, the API key's user) · Luis Candiales
`4369d420-89d1-40e4-96aa-9091d96fb94b` (Dispatcher, writes the proposals) · Antonio Brown (technician).

### Job filter params that work
`GET /jobs?count=100&page=N&filter.category=<category_uid>` and `filter.customer=<customer_uid>` (both verified).

---

## 7. Open questions / blockers

1. **Simple Flow has no CPQ trigger and no KPI questions.** Should proposals for Simple Flow jobs be drafted at
   "Proposal Creation" by the agent (POST /estimate), and should the KPI fields still be filled?
2. **Which template for which job?** In practice Luis uses *Tile Leak Repair* for almost everything (100 of 128), including flat
   and shingle jobs, and rewrites the GOOD/BETTER text. Confirm whether the agent should pick by roof type
   (Flat → Flat template, Tile → Tile, replacement → Roof Replacement, otherwise Custom) or copy Luis.
3. Pricing source: the "Retail Value / Special AHS Member Price" numbers are set by hand per job. There is no price book
   or formula. The agent needs a pricing rule or must leave prices for a person.
4. Note text missing from `GET /jobs/{uid}/note` on the sampled jobs (section 6).
5. The test customer's mobile changed to +19549147244. Confirm it is the user's.
6. The repo's Zuper client denylists every write to `/estimate`. Creating proposals needs a new, narrow, explicit scope.
7. The CPQ "Repair items needed" lookup on the Flat template is being built today. It may change the template structure again.
