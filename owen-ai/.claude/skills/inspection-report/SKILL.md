---
name: inspection-report
description: Make the customer inspection report (HTML + PDF, Dream Team design) for a job after the inspection — photos with circles, plain-English findings, the proposal's options. A DRAFT for Owen to download from the chat; never sent to the customer. Use for "make the inspection report for job 728", "reporte de inspección", "build the customer report".
---

# Inspection report

Read `knowledge/inspection-report-guide.md` first (the 10 rules). Diagnosis help: `knowledge/roof-leak-playbook.md`
(§0 core rules, §2 symptom → cause, §3 cause cards, §5 severity).

1. **Facts:** `mcp__owen__job_dossier` — stage, notes, proposal options (what each option includes), chats
   (what the customer reported: which rooms, when it leaks). Note room names and any contradictions.
2. **Photos:** `mcp__owen__job_photos` with `which="inspection"`. **Read every photo** you might use. Note for
   each: what it shows, its number, and its time (duplicates!). Look for the inspector's own circles/numbers.
3. **Diagnose:** inside stains → trace upslope → the joints (walls, vents, valleys, pipes) first. Pick the most
   likely cause; rank the other findings; decide severity (now / soon / watch).
4. **Match the money:** for each finding, which option includes its fix? If none: "ask us for a price".
5. **Write the spec** (guide §2–§5): badge, what_happened, hero, location, findings (worst first, 1–2 photos each
   with marks as 0–1 fractions), inside list, not_done, weather, inspected_how. Run the guide's §6 checklist.
6. **Call** `mcp__owen__make_inspection_report` with the spec. If it returns validation errors, fix and retry.
7. **Reply to Owen:** the download links, a 3-line summary, warnings (placeholders, contradictions, unpriced advice,
   anything not verified), and offer edits. Never say it was sent — it was not.

Edits later ("move the circle on photo 21 up", "drop finding 3", "make it in Spanish"): change the spec and call the
tool again; it makes a new version with new links.
