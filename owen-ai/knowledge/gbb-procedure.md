# Good / Better / Best — what we actually do (for the after-inspection report)

Source: Owen, recorded 2026-11-12, and the approved job #292 report. Use this for each option's
`scope`, its picture `steps` and the `compare` rows. **No prices anywhere in the report.** They are
shown only after AHS says how much it covers.

## The rule

**GOOD always stops the leak.** BETTER and BEST do the same work and add more: more of the roof
(more leaks, a wider area around each one), stronger materials, more days, a longer warranty. Every
option in the report must match what that option includes in the Zuper proposal. Never add work
the proposal does not price; label it "ask us for a price" instead.

## GOOD: Owen's steps, in his order

1. **Find the leak.** Inspect the area that is leaking, from the attic when possible, to find
   exactly where the water comes in.
2. **Remove the tiles by hand.** Gently, one at a time, so they do not break. Set them aside.
3. **Inspect the underlayment.** Follow the water marks to where the leak starts.
4. **Remove the old underlayment.** The wet, worn layer comes off around the leak.
5. **Clean and inspect the deck.** Clean it, then check the plywood is solid.
6. **New waterproof underlayment.** Tied into the roof around it, with the seams sealed.
7. **Tiles back on, the same day.** Matching replacement tiles where one is broken.
8. **Cleanup** (with a magnetic sweep for nails), **a walk-through with the customer**, and **we
   ask for a review**.

The picture has room for 6 steps: merge 3 + 4 ("Inspect & Remove Old Underlayment") and put
cleanup + walk-through in the last one ("Tiles Back & Walk-Through").

## BETTER: what it adds (Owen + the #292 proposal)

- All the leaks the inspection found, not only the main one.
- A **membrane** (Peel & Stick, self-adhered) instead of plain underlayment.
- Double sealing of the laps, transitions and flashing.
- A **two-day** repair: the sealed area is left to dry overnight before the tiles go back.
- A longer warranty (the proposal's).
- Owen also named a "**bull**" step and "**grinders**". Their meaning is NOT confirmed yet, so do
  not put them in a report until Owen explains them.

## BEST: what it adds

- An **expanded area** around every leak: a wider area opened, to cut off water that travels
  sideways under the tiles. The coverage photo must SHOW the bigger area; that is the point of BEST.
- Two layers of Peel & Stick, and a full flashing overhaul.
- Two days; a photo record of the finished work; the longest warranty.

## Coverage areas on the real photos

- One to three photos per option. Use the technician's own marked photos (companycam_photos
  `marked_path`) to find WHERE the leaks are, then draw the areas on a PLAIN copy (`path`).
- GOOD: the one area it fixes is `covered`. The leaks it does not fix are `excluded` (dashed
  grey), labelled e.g. "Terrace leaks - not included".
- BETTER: every leak area is `covered`.
- BEST: the same leaks with visibly LARGER areas.
- When you are not sure where a leak is (e.g. which mark is "the bathroom"), say so to Owen. Never
  guess silently. The page shows "the highlighted areas are approximate".

## The pictures (`picture`)

- Title words come from `name` + `headline` (e.g. "GOOD - Targeted Leak Repair Above the
  Bathroom"). 4-6 `steps`, each a short title (2-5 words) + one line. 2-3 `callouts`, 3-5 `layers`
  top to bottom (e.g. "Barrel Tile", "New Waterproof Underlayment", "Sealed Seams", "Roof Deck
  (Plywood)"), a `top_label`.
- `roof` describes what to draw (e.g. "terracotta barrel tile main roof and a flat terrace roof
  beside the pool"). `notes` says what NOT to draw (e.g. "no vent pipe in this repair").
- Never a customer name, address or price in it.
- After the tool makes a picture, READ it and compare every word with the spec. A misspelled word
  means regenerate that option (`update_inspection_report`, `regenerate_pictures`).
