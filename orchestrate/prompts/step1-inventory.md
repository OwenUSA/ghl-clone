Produce a frontend test-surface inventory for this project. Write it to `{{OUT}}`.

READ FIRST, before opening any source file:

- `CLAUDE.md`
- `DECISIONS.md` — these are locked decisions. Do not contradict them, do not re-litigate
  them, do not propose changes to them. If something in the code looks wrong but is
  explained there, it is not wrong.
- `AUDIT.md`, `NORMALIZATIONS.md`, `design-tokens.md`

Those documents already describe how this system works. Your job is NOT to re-derive that.
Your job is to turn it, plus the frontend source, into an enumerated list of things that
can be tested through a browser.

## Output shape

For every route/view in the frontend, one section containing:

- **Route** — the URL path, and how you reach it from the UI.
- **Purpose** — one line.
- **Interactive elements** — every button, link, input, select, tab, modal trigger,
  drag-and-drop target, sortable column, infinite-scroll or paginated container. Give a
  stable selector or accessible name for each, so a Playwright script can find it without
  guessing.
- **Expected behaviour** — for each element, what SHOULD happen. This is the assertion a
  tester will write. Be specific: "clicking Save closes the panel and the new name appears
  in the list row" beats "saves the contact".
- **Writes?** — mark every element that mutates data. Testers need this to know what they
  must clean up.
- **Role sensitivity** — where behaviour should differ between ADMIN, DISPATCHER and TECH.
  A TECH can move an opportunity between stages and send a message, but cannot edit
  records. Note where the UI should reflect that.

End with a short section, **Data preconditions**: which views need existing records to show
anything at all. This matters because the production database is empty.

## Rules

- Do not run the app, do not start a browser, do not test anything. This step is inventory
  only.
- Do not modify any file except `{{OUT}}`.
- Prefer completeness over prose. A missed element is a surface nobody tests.
- Where you are unsure whether behaviour is intended, say so in the expected-behaviour line
  rather than asserting. A tester treating your guess as truth produces a false bug report.
