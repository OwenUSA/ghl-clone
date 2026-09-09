Fix the triaged findings in `{{MERGED}}`, one at a time. Record what you did in `{{LOG}}`.

A human has already triaged that file. Work **only** entries with `classification: bug`.
Ignore `environment` entries entirely. For `uncertain` entries, investigate but do not
change code unless you can state concretely what is broken.

## Before you start

1. `git status` must be clean. If it is not, stop and say so.
2. Create and switch to `{{BRANCH}}`.
3. Read `CLAUDE.md` and `DECISIONS.md`. `DECISIONS.md` is locked: if a finding asks for
   something it forbids, mark the finding `blocked` with that reason. Do not overrule it.

## One at a time

For each bug, in severity order:

1. **Reproduce it locally first.** Start the backend and frontend per `CLAUDE.md` and
   confirm the actual behaviour matches the report. If it does not reproduce locally, mark
   it `needs-prod-verify` and move on — do not fix a bug you cannot see.
2. **Fix the cause, not the symptom.** Match the surrounding code: its naming, its comment
   density, its idiom.
3. **Add or extend a test that fails before your fix and passes after.** This project
   asserts behaviour, not status codes: a filter must actually narrow the set, a 403 must
   not have mutated anything, a suppressed send must write no row. A test that only checks a
   status code would have missed three of the bugs found while building this — do not add
   one of those.
4. **Run the gates**: `uv run ruff check .`, `uv run pytest`, and `npm run build` in
   `frontend/`.
5. **Commit** just that fix, with a message saying what was broken and why the fix is right.
6. Append to `{{LOG}}`.

**Two attempts per finding.** If it is not fixed and green after the second attempt, revert
your attempt for that finding, mark it `blocked` with what you tried and what stopped you,
and move to the next one. Do not keep going on one item.

**Stop the whole run** if `pytest` or `ruff` or the frontend build was green and you cannot
get it green again. Say so plainly rather than committing over it.

## Hard limits

- **Branch only.** Do not push. Do not `ssh owen-main`. Do not run `deploy.sh`. Do not merge
  to `main`. Someone reviews this branch before it goes anywhere.
- **A fix that needs an Alembic revision: flag it, do not apply it.** Mark the finding
  `blocked` with `needs-migration`. Migrations do not run on container start and cannot be
  verified against production from here. If you do generate a revision for review, declare
  `server_default` on any new non-nullable column, or the `ALTER TABLE` fails against
  existing rows.
- **Do not touch production.** No writes, no records, no containers.
- Do not run `python -m app.seed` — it calls `drop_all()`.
- Do not reformat, refactor or tidy code unrelated to a finding. A large diff hides the fix.

## Log format

Append one entry per finding to `{{LOG}}`:

```
### F<id> — <title>

- status: fixed | blocked | needs-prod-verify | not-a-bug
- reproduced-locally: yes | no
- cause: <what was actually wrong>
- change: <files touched, and why this is the right fix>
- test: <the test that now covers it, and how it failed before>
- commit: <sha>
- notes: <for blocked: what you tried and what stopped you>
```

`needs-prod-verify` is the honest answer for anything that only manifests on the deployed
stack — Traefik, cookie domain, build output, HTTPS-only behaviour. Those cannot be closed
from here. Marking one `fixed` because the code looks right is worse than marking it
`needs-prod-verify`.

Finish with a summary: how many fixed, blocked, needs-prod-verify, not-a-bug, and the state
of the gates. If you left something undone, say which and why.
