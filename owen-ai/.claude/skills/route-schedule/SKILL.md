---
name: route-schedule
description: Make the technicians' routes / schedule with a map — who goes where tomorrow or this week — with the company's rules (one-day customers first, nearby customers together, repairs only with the customer's OK, call before an inspection, rain flagged). Also when Owen says which days a technician works or is off. Use for "make tomorrow's routes", "plan the week", "who goes where", "schedule the jobs", "arma las rutas", "haz la agenda de mañana", "programa la semana", "Owen works Thursday", "Marx is off Friday", "Owen trabaja el jueves".
---

# Route schedule

The plan comes from the CRM's planner — the same one as Dispatch → Remake the schedule — so the
office sees the same plan. **It is a DRAFT: nothing is booked and nothing reaches Zuper.** The
office books each visit in Zuper after agreeing it with the customer.

## The owner's rules (built into the planner — explain them, never override them)

1. A customer who can only do ONE day gets that day first.
2. Customers close to each other share a day, so the technician drives less.
3. A **repair** goes on the schedule only when the customer agreed: a proposal accepted / signed
   in Zuper, AHS authorized it (AHS Approved, AUTHO #), or the customer said yes on a call or
   text. Callbacks too.
4. Before an **inspection** somebody must call the customer to get / verify the information. An
   inspection with no such call is placed, marked **CALL FIRST**.
5. Crew: Antonio (inspections and repairs) and Marx (repairs only), **2 jobs a day each**. Owen is
   ON CALL: he works only the days he says.
6. Weather is a **flag only** — never a reason to move a visit. Mention rain for the next day first.

## Steps

1. **Days first.** If Owen says who works when ("I'll work Thursday", "Marx is off the 28th",
   "Owen trabaja jueves y viernes"), call `set_crew_days` before planning:
   - Owen (on call): `working` with the dates. **Ask how many jobs a day** he will take unless he
     said it; pass `max_per_day` (default 2 if he says "the usual").
   - Anyone else: `off` with the dates. Removing a range: `remove`.
   - Echo the saved days back (weekday + date). `crew_days` shows the crew and the next 14 days.
2. **Plan.** `plan_routes` — `days: 1` for tomorrow only, `6` for a week (business days from
   tomorrow). Default boards are AHS Simple Flow + Retail.
3. **Repairs waiting for the customer's OK.** For each job in `waiting_for_customer_ok`, read
   its `last_calls_and_texts`; when one is not enough, `job_dossier` for that job. Only a CLEAR
   yes from the customer (their words, with the day: "yes, go ahead with the Better option",
   "sí, háganlo") counts. Then call `plan_routes` again with
   `agreed: [{job_number, source: call|text|other, quote: their exact words, said_at}]`.
   - Never pass a yes without their words, and never for "let me think", "how much?", a price
     question, or a yes to a DATE only (that is a booking, not an agreement to the work).
   - AHS jobs waiting on AHS: say so; do not read Frontdoor for every job (a customer review does
     that — `start_customer_review` — if Owen asks).
4. **Answer** (Owen's language):
   - First line: the plan link line exactly as `plan_routes` returned it
     (`[Plan #N — map and list](/dispatch?tab=book&plan=N)`). The map and the week appear under
     your answer; do not redraw them.
   - **Per day, per technician**: a short table — time, job, customer, city, inspection/repair,
     why it is OK to go (the repair's OK / the inspection's call), CALL FIRST, rain.
   - **CALL FIRST** list (call before going: verify address, second phone, appointment window).
   - **Waiting for the customer's OK**, grouped: waiting on AHS / waiting on the customer
     (proposal not accepted) / no proposal yet — each with what was checked.
   - **Not placed** and why (a one-day customer whose day is full: say which day and who is full).
   - **Rain**: the next day first, then later days ("less certain" beyond 3 days). If the weather
     agent is off, one line saying so.
   - End with: nothing is booked; the office books each visit in Zuper.

## Never

- Never say a visit is booked, scheduled or confirmed — it is proposed.
- Never change the crew unless Owen told you to.
- Never move a visit for rain; never leave a job out for rain.
- Never invent a job, a time, a yes or a phone call: only what the tools returned.
