# Zuper knowledge for Owen AI — index

Owen AI manages Dream Team Roofing FL's Zuper field-service account (jobs, notes, chats, quotes,
proposals). Built 2026-11-02. Facts carry dates; newer live facts win.

| File | Read it when |
|---|---|
| `safety-rules.md` | **First, every session, and before ANY non-GET request.** What is forbidden, what needs the owner's OK, how to make an approved write (backup → one request → read back → never retry) |
| `live-account.md` | Next (written separately by a live probe): current boards/columns, active workflows, config, counts. Wins over the other files when they disagree |
| `operations.md` | To understand the business: boards and column meanings, the AHS warranty flow, people/roles, checklists and fields, line-item codes and reports, texting history, automations, routes, test jobs |
| `api-reference.md` | Before calling an endpoint: method/path/body, response shape, V/C/D/X marker (verified live / code-only / docs-only / fails), gotchas, doc-vs-observed discrepancies (§15) |

Quick task map:
- Read a job / its history / fields → api-reference §1; meaning of its column → operations §3.
- Read chats and call summaries → api-reference §11 (5 allowed Connect reads only).
- Read prices quoted to a customer → api-reference §8 (`GET /estimate/{uid}` options/line items); codes → operations §7.
- Add a note → safety-rules §3 + api-reference §2.
- Draft a proposal → operations §7.1 rules, api-reference §8, safety-rules §3 (DRAFT only, never send).
- Configure something → safety-rules §3–4 (owner OK, backup, rehearse on AHS - TEST).

Source documents (repo): `docs/ZUPER-OPERATIONS.md`, `ZUPER-AUTOMATIONS.md`,
`ZUPER-CUSTOMER-TEXT-WORKFLOWS.md`, `ZUPER-KPI-SETUP.md`, `ZUPER-NEXT-UI-MAP.md`,
`ZUPER-SPEED-TO-LEAD.md`, branch `docs/zuper-2026-10-12-evening` (`ZUPER-PROPOSALS-AND-UPSELLS.md`),
`backend/app/zuper/client.py`, `DECISIONS.md`. Official docs: https://developers.zuper.co (index
`https://developers.zuper.co/llms.txt`; every reference page has a `.md` with OpenAPI JSON).
