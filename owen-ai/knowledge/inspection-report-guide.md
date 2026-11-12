# Inspection report guide — Dream Team Roofing (for `make_inspection_report`)

How Owen AI writes the customer inspection report after a visit. Built from two approved samples (jobs #710 and
#728, Nov 2026) and two independent reviews of them. Read with `roof-leak-playbook.md` (diagnosis, cause cards,
analogies, severity, legal wording). **When the two differ, this file wins.**

The report is for **Owen to review**. You never send it to anyone. The page itself is clean (no draft banner, no
"[placeholder]"): a missing header value (phone, license, city, inspector) is simply left out, so **always list the
tool's warnings to Owen in the chat**. Owen downloads it from the chat and decides.

---

## 1. The ten rules that matter most (each one was a real mistake in a draft)

1. **Never claim a check that was not done.** No "we traced it", "the cause is", "confirmed" unless the photos show
   an attic check, a water test or an opened roof. Default wording: *"the most likely cause … we'll confirm it when
   we open the area."* List what was not done in `not_done` (usually "attic, water test").
2. **The fix you describe must match what the proposal prices.** Read the proposal's option texts (dossier) before
   writing "How we fix it". If the right fix (e.g. new metal counter-flashing) is not in any option, say so plainly:
   *"Our advice for a longer-lasting fix: … It is not in your current options — ask us for a price."* Never promise
   unpriced work as part of an option.
3. **Count from distinct objects, not photos.** Photos taken seconds apart from the same spot show the SAME thing.
   Check timestamps before writing "two vents", "three cracked tiles". When unsure: "a few", "several", "one … (photos
   11, 13)".
4. **Only claim what a photo shows.** "Open joint" → if you only see tar: "sealed only with tar that is breaking up".
   "No flashing" → "we could not see any metal flashing". Stain colors: describe, don't diagnose ("darker tiles, a sign
   water runs over them often").
5. **Use the room names the job uses.** Take them from the proposal/notes/chats (bathroom, living room, garage). If
   the proposal contradicts itself, use neutral wording ("one leak area") and put the conflict in your chat answer to
   Owen, not in the report.
6. **Normal is not a defect.** A flat sheet in a tight corner, weathered mortar on an old roof, staining under a valley
   are normal or cosmetic unless something failed. Say what is normal so the customer isn't alarmed or misled.
7. **Use the inspector's own markings.** If a photo already has his circles/numbers ("1", "2"), use it as is (no extra
   marks) and say "our inspector marked this spot as area 1 (Photo 21)".
8. **Rule out look-alikes in one line** when it helps trust: water heater below the stain, bathroom fan, AC line —
   *"No — signs the stains come from the water heater (it sits on the floor, below the stain)."*
9. **Numbers only from data.** Prices, warranty, deposit, AHS credit come from the proposal (the tool fills the table;
   you don't type prices). When the proposal has no AHS credit and the job has an `AHS Job ID`, the tool takes what
   AHS authorized from Frontdoor and says so in its warnings — pass that warning to Owen; a proposal whose credit
   differs from Frontdoor's keeps its own and gets a warning too. Sizes only if measured; otherwise "about one sheet", "a small area".
10. **One clear next step.** Page 1 ends with ONE call to action and a time frame ("within the next 2 weeks" for Fix
    now). Add the "ask us for a price" item there too if there is unpriced advice.

## 2. Structure (what the tool renders from your spec)

| Part | What you provide | Notes |
|---|---|---|
| Header | — | Logo, company, phone/license (settings), customer name + service address, job #, date: **the tool fetches these from Zuper**. |
| Badge | `severity` + one sentence (or `severity: "hide"` = no badge at all — when Owen asks to remove it, use hide, never "none", which prints "No repair needed") | Worst finding's level. "Water has gotten into your home. The most likely cause is … and it can be repaired." |
| What happened | 3–5 short sentences | Story order: where → what's wrong → how water gets in ("we believe") → what it caused inside → "we'll confirm when we open it". |
| Hero | cause photo + inside photo | Best close-up of the main cause (with marks) next to the worst interior stain. |
| Location | one wide photo + one sentence | A photo that shows WHERE on the house (street/house visible). |
| Findings | 1–8 cards, worst first | Each: title, severity, 1–2 photos with marks, refs (all photo numbers), saw / means / why / like / fix / prevent. |
| Inside | photo + Yes/No/Not checked list | Honest list incl. "Not checked — attic". |
| Options | — | **Not in the report** (owner, 2026-11-09): no price table and no signature line. The proposal goes to the customer separately; "How we fix it" may still name which option includes the fix, never a price. |
| Photo pages | `galleries` | e.g. "Inside the house", "Cracked tiles marked by our technician" (the technician's marked copies). |
| Good / Better / Best | `gbb` | Per option: an AI picture of the work, the area it covers drawn on real photos, the written scope, days, warranty (from the proposal). Then a comparison. **Still no prices** (2026-11-12): they come after AHS says how much it covers. See `gbb-procedure.md`. |
| Until it's fixed / legal | optional overrides | Defaults come from the playbook §4 and §8. |

## 3. Writing style

- 6th–8th grade. Short sentences. Define a roofing word the first time ("the hip (the ridge that runs down a corner)").
- Title of a card = plain words + where: "Where the garage roof meets the house wall — most likely the main leak".
- **What we saw** = what a camera recorded. **What it means** = your opinion, marked as such ("we believe", "likely").
- **Think of it like this**: one analogy (playbook §7 list, or: caulk around a bathtub, grout in a shower, window
  with a cracked seal, river channel for valleys, raincoat collar for flashing).
- **How we fix it**: the priced fix + which options include it ("included in all three options", "named in Best").
- Use "we", warm and direct. No fear words ("dangerous", "catastrophic"). No blame on previous roofers — say
  "this spot has been patched before".
- English by default. If Owen asks for Spanish, write the whole spec in Spanish.

## 4. Severity (always these three, playbook §5)

| Level | Use when | Example |
|---|---|---|
| `now` Fix now (red) | water is entering, interior damage, or safety | the joint above the stained ceiling |
| `soon` Fix soon (orange) | will become a leak; fix in the next few months | open valley gaps, crumbling cap mortar, cracked tiles |
| `watch` Watch (blue) | normal aging; check at next service | worn ridge mortar with caps still in place |

## 5. Photos and marks

- Pick photos with `job_photos` (inspection set), then **Read each one** before choosing. Never mark a photo you
  didn't look at.
- Marks: 1–3 per photo. `box` = fractions of the image [x0, y0, x1, y1] (0 = left/top, 1 = right/bottom). Ellipse
  for an area, line for a joint/gap, rect for a band (ridge line). Labels ≤ 24 chars, 1–3 words ("Open gap",
  "Tar patches", "Crumbling mortar").
- Colors: red = the cause / damage, orange = secondary problem, blue = watch item, green = healthy example.
- Put the box generously around the defect; the label sits above it (or below if near the top edge).
- A dark interior photo: request `brighten` and say so in the caption ("brightened to show the faint spot").
- Skip blurry, duplicate, people, documents, payment screenshots, other houses (check GPS/time if unsure).

## 6. Before you call the tool — checklist

- [ ] Every claim maps to a photo number I actually looked at.
- [ ] Counts checked against timestamps (no duplicates counted twice).
- [ ] "Most likely … we'll confirm" — no unverified trace.
- [ ] Every "How we fix it" is in a priced option, or labeled "ask us for a price".
- [ ] Room names match the job; conflicts reported to Owen in chat.
- [ ] Not-done checks listed; inside checklist honest.
- [ ] No prices anywhere in my text (the report has no price table).

## 7. After the tool returns

Answer Owen in chat with: the two download links, a 3-line summary (badge, findings with severity), **warnings**
(placeholders missing, proposal contradictions, unpriced advice, anything you could not verify), and offer edits
("I can move a circle, drop a finding, or switch to Spanish").
