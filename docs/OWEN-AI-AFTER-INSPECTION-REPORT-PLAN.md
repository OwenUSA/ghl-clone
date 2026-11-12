# Owen AI after-inspection report — plan (2026-11-12)

Owen AI makes the after-inspection report approved on Zuper job #292 (the draft stays on the
user's own computer; it holds a real customer's photos). It builds on the existing tool
`make_inspection_report` (`backend/app/owen_ai/report.py`, 2026-11-07). Start implementing from
an up-to-date `origin/main`: the local checkout was 163 commits behind when this was written.

## Decided

- **No prices.** Prices are shown only after AHS says how much it covers: no price box, no
  "you pay", no money in the pictures. This matches the owner's 2026-11-09 rule in
  `owen-ai/knowledge/inspection-report-guide.md`.
- **Everything is editable after it is made**, by hand on the page or by asking Owen AI in the
  chat. Every change is a new version, and any version can be restored.
- **GOOD always stops the leak.** BETTER and BEST add work on top: more leaks fixed, stronger
  materials, a bigger area, a longer warranty.
- **It stays a draft.** It is never sent automatically, nothing is written to Zuper, and
  CompanyCam is only read.
- **No unpriced work.** The report promises only what the proposal's options include; anything
  else is labelled "ask us for a price".

## What the report contains

1. **Cover and summary:** customer, address and inspection date, all read from Zuper, plus the
   leaks found, in plain words.
2. **What we found:** inside photos, roof photos with the technician's marks, cracked tiles.
3. **One block per option (GOOD / BETTER / BEST):**
   - the AI picture: work sequence, finished overview, section detail, top view, warranty;
   - the area covered, highlighted on the real roof photos in the option's colour, with work
     not included dashed grey;
   - the full written scope, warranty and time on site.
4. **Comparison:** leaks fixed, membrane, sealing, repair time, photo record, warranty. No
   price row.

## How editing works

- **One saved report per job, with versions.** The page is always drawn from the latest
  version, so hand edits and AI edits change the same thing.
- **Ask Owen AI:** "move the bathroom area up", "add a step to BETTER", "redo the BEST
  picture", "make it in Spanish". It reads the latest version, including Owen's own edits,
  changes only what was asked and saves a new version.
- **Edit on the page (a new Reports tab):**
  - change any text;
  - add, remove or reorder photos;
  - drag the corners of a highlighted area, or switch it between covered and not included;
  - edit an option's steps.

  A live preview shows the change before Save.
- **Pictures:**
  - If an option's steps change, its picture is marked out of date. Owen regenerates it
    (about $0.18) or uploads his own.
  - A picture is never redrawn without being asked.
- **Simultaneous edits:** a save carries the version it started from. If Owen AI and Owen
  change the report at once, the stale save is refused and that side re-reads the report.

## Build order

1. **Knowledge: `owen-ai/knowledge/gbb-procedure.md`**
   - Owen's GOOD steps from the 2026-11-12 recording: attic inspection, tiles off by hand,
     inspect then remove the underlayment, clean and check the deck, new underlayment, tiles
     back the same day, cleanup, walk-through, ask for a review.
   - BETTER and BEST from the Zuper proposal plus what Owen said: membrane, a second day to
     dry, a bigger area, flashing.
   - "Bull" and "grinders" go in only once Owen explains them.
2. **CompanyCam photos for Owen AI**
   - Today `owen_ai/photos.py` reads Zuper only. On #292 the inside photos and the technician's
     marked photos ("2 old repairs the leaks is back", cracked tiles #1–#9) were only in
     CompanyCam.
   - Read the linked project too, read-only, including the `_annotated` copies, through
     `companycam.py`.
3. **Versioned reports**
   - Two new tables: `owen_ai_reports` (one per job) and `owen_ai_report_versions` (spec JSON,
     source ai|manual, author, note, base version, files, cost, created_at). Needs an Alembic
     revision.
   - Stop the 90-day removal of the current version; only old versions' files are cleaned up.
   - New tools: `get_inspection_report` and `update_inspection_report` (with base_version).
     Making a report creates v1.
4. **New spec sections and pages (`report.py`, `assets/report.css`)**
   - Found galleries.
   - Option blocks: picture steps (6), callouts (3), section layers (4), top-view label,
     written scope, warranty, days.
   - The comparison page.
   - A new `area` mark: a polygon of 0–1 fractions (3–12 points), with kind
     covered|excluded|expanded.
   - Facts (customer, address, date, options, warranty) still come from Zuper only.
5. **Option pictures: `owen_ai/infographics.py` (new)**
   - One gpt-image edit per option, with the approved #292 layout stored in `assets/` as the
     reference.
   - Its own switch, off by default. It shares the key and cost logging with `pictures.py`.
   - The prompt holds only the work: no customer name, address or price.
   - Read the picture's text back and compare it with the spec; flag any misspelling.
   - Cache each picture by a hash of its option's picture spec, and record the cost on the
     version.
6. **Reports tab: `components/owenAi/ReportsView.tsx` and `owen_ai/api.py`**
   - The list, preview, PDF download, version history and restore.
   - The editor, built in steps: text first, then photos and the area editor, then picture
     controls.
7. **Skill, tests and the trial**
   - Skill order: dossier, then photos (Zuper + CompanyCam), read every photo, find the leaks
     from the technician's marks, draw the areas, make the report, check the pictures, then
     reply with the links and anything unconfirmed.
   - Tests:
     - area shapes are validated, and no price appears anywhere;
     - facts come only from Zuper;
     - a stale save is refused, restore works, and a manual edit survives an AI edit;
     - with the switch off, no OpenAI request is made;
     - no customer data goes into the prompt;
     - the page renders with no network;
     - `ai_guard` / `zuper_guard` still hold.
   - Trial on #292, #710 and #728. Owen reviews before it is used on real jobs.

## Cost per report

- **Three option pictures:** about $0.54 when the report is made.
- **One picture redone:** $0.18, only when asked.
- **Text and area edits:** free.
- **Owen AI turns:** run on Owen's Claude plan, as today.

## Still to answer

- **"Bull" and "grinders":** what are they (the BETTER steps)?
- **Who edits:** Owen AI is owner-only. Should the office (ADMIN) edit reports too?
  Recommended: Owen + ADMIN.
- **Text in the pictures:** planned as AI-written text with a read-back check. The other
  choice is text drawn by our code (exact, but a different look).
- **A priced version later:** it could be triggered by the AHS authorization feed
  (`app/ahs_authorizations.py`). Not in this plan.
