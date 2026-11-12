---
name: inspection-report
description: Make or change the customer after-inspection report (HTML + PDF, Dream Team design) for a job — photos with circles, plain-English findings, photo pages, and the Good / Better / Best pages (an AI picture per option, the area it covers on the real photos, the written scope). NO PRICES. A DRAFT for Owen, saved with versions; never sent to the customer. Use for "make the inspection report for job 728", "reporte de inspección", "build the customer report", and for any change to one ("move the BEST area", "change step 3", "redo the BETTER picture").
---

# After-inspection report

Read `knowledge/inspection-report-guide.md` first (the 10 rules) and `knowledge/gbb-procedure.md`
(what each option really does, and how to draw the areas). Diagnosis help:
`knowledge/roof-leak-playbook.md` (§0 core rules, §2 symptom → cause, §3 cause cards, §5 severity).

## Make one

1. **Facts:** `mcp__owen__job_dossier` — stage, notes, the proposal's options (what each one
   includes), chats (what the customer reported: which rooms, when it leaks). Note room names and
   any contradictions.
2. **Photos, from BOTH places:** `mcp__owen__job_photos` (`which="inspection"`) AND
   `mcp__owen__companycam_photos`. CompanyCam often has what Zuper lacks: the technician's MARKED
   copies (circles, numbers, notes written on the photo, `marked_path`) and photos inside the house.
   **Read every photo** you might use. Note what each shows, its number and time (duplicates!), and
   every mark or note the technician wrote. Those marks tell you where the leaks are.
3. **Diagnose:** inside stains → trace upslope → the joints (walls, vents, valleys, pipes) first. How
   many leaks, where, and which is which (e.g. "the bathroom"). If you cannot tell, say so to Owen.
4. **Match each option:** what each option fixes and adds, from the proposal and
   `gbb-procedure.md`. No prices anywhere: they come after AHS says how much it covers.
5. **Write the spec** (guide §2–§5):
   - badge, what_happened, hero, location, findings, inside, not_done, weather, inspected_how;
   - `galleries`: e.g. "Inside the house", "Cracked tiles marked by our technician";
   - `gbb`:
     - per option: headline, picture (6 steps, callouts, layers, top label, roof, notes), the full
       written `scope`, days, and `coverage` (areas drawn on PLAIN photos: `covered`, or
       `excluded` for what GOOD leaves);
     - then `compare` rows: leaks fixed, membrane, sealing, repair time, photo record, warranty.

   Run the guide's §6 checklist.
6. **Call** `mcp__owen__make_inspection_report`. If it returns validation errors, fix them and retry.
7. **Check the pictures:** Read each picture file the tool lists and compare every word with the spec.
   A wrong word means `update_inspection_report` with `regenerate_pictures` for that option.
8. **Reply to Owen:**
   - the links and a 3-line summary;
   - warnings: placeholders, contradictions, unpriced advice, leak locations you are not sure of,
     pictures switched off;
   - that he can change anything on the **Reports** tab or by asking you.

   Never say it was sent: it was not.

## Change one (Owen asks in the chat)

1. `mcp__owen__get_inspection_report` (report id, or the job number). Owen may have edited it on the
   page: what it returns is the truth.
2. Change ONLY what he asked in that spec. Keep everything else exactly as it is, including his edits.
3. `mcp__owen__update_inspection_report` with `base_version` from step 1 and a short `note`.
   - If it says the report changed meanwhile, start again at step 1.
   - Regenerate a picture only when he asked for it, or when its words changed and he wants it
     updated. Each one is about $0.18.
4. Reply with the new links and what changed.
