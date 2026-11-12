# Proposal drafting guide (Good / Better / Best leak repair, for `propose_proposal_draft`)

How Owen AI drafts a Dream Team Roofing GOOD/BETTER/BEST proposal from a job's inspection photos.
Sources: 120 real proposals DTR-15 to DTR-138 (204 options, `owen-ai-analysis/proposal-photo-audit/pricing_analysis.md`
+ `pricing_table.csv`), 59 photo audits of those jobs (273 drafting lessons), `zuper/live-account.md` §1-2, and
the templates as checked on 2026-11-07. Read with `inspection-report-guide.md`: **its 10 accuracy rules apply here
too**: no unverified claims, count distinct objects (not photos), only what a photo shows, the job's own room
names, numbers only from data.

**What you make is a PENDING draft in the CRM.** Owen approves it on the **Proposals** tab. Only then is it created
in Zuper, and only as a Zuper DRAFT. Nothing is ever sent to a customer. Never say a proposal was "created",
"sent" or "in Zuper". Say it is "waiting for your approval on the Proposals tab".

**Price rule (owner, 2026-11-09):** prices Owen gives in the chat **always win**. Without them, estimate each
option from past prices (§4) and show Owen the basis (the comparable DTR numbers and their prices). This is the
one exception to the operating manual's "never estimate a price": it covers proposal drafts only, and every estimate
is labelled as an estimate.

---

## 1. Template: ALWAYS the Short Proposal template (owner, 2026-11-09)

Every draft uses **"DTR - Tile Leak Repair - Short Proposal (Good / Better / Best)"**
(`short`, uid `0e5d788e-656e-40f5-a4af-cb03ffc92fc0`). The tool forces it, whatever you send. It has the same options
as the Tile template, DTR-CUSTOM-GOOD / DTR-CUSTOM-BETTER / DTR-TILE-BEST, on the short layout "DTR Proposal - Short"
(no logo, financing, CertainTeed, comparison or letter pages).

The other four DTR templates (Tile, Flat, Custom, Roof Replacement) still exist. Read old proposals with them, but
never draft on them.

**Because the BEST line's product is "Tile Leak Repair", the roof type still matters in your TEXT:**
- Write the scope for the real roof material at the leak: tile, flat / modified bitumen, shingle, metal. The history
  put tile wording on about 20 non-tile roofs (DTR-31, 41, 45, 49, 62, 77, 78, 81, 119, 124, 126, 128, 130, 133,
  135, 136, 137, 60). Never write tile steps on a non-tile roof.
- When the leak is NOT on tile, say so in `note` for Owen: "Roof is <material>; the Short template's BEST product
  is named Tile Leak Repair — rename the line in Zuper if needed."
- A roof replacement: never estimate a price; ask Owen.

- **Mixed roofs: choose by where the failure is.** Tile lifted at a tile/flat junction → `tile`, plus a flat
  tie-in line. The patch is on the flat cap sheet → `flat`. Shingle slope dying into a flat section with the leak at
  the break → the template for the side that fails. Put the transition in GOOD and name both surfaces.
- Name the material correctly. Terracotta barrel tile set in mortar is **clay**, not concrete. A granulated surface
  with a white coating is **modified bitumen / roll roofing**, not TPO. Never write "tiles or shingles".
- **No roof photo → no template.** Stop and ask (§5.1).

---

## 2. Tier structure (Luis's canonical scope)

Each option is ONE package line (qty 1). The option's real title goes in `option_description`. The scope is HTML
in `scope_html`, built in this order:

```html
<p><strong>Scope of Work</strong></p>
<ul>
  <li>(Retail Value: $R | Special AHS Member Price: $M | AHS Authorized Credit: -$C | Customer Pays Out of Pocket: $P | Total Savings: $S)</li>
  <li><strong>Step Title:</strong> what we do …</li>      <!-- 5-8 steps -->
  …
</ul>
<p>Workmanship Warranty: 1 Month This warranty covers workmanship on … for 1 month from completion.</p>
<p>Payment Terms: 50% Upfront ($X.XX) – due upon acceptance of proposal. 50% Upon Completion ($X.XX) – due after final inspection and customer approval. (AHS portion billed directly to AHS).</p>
```

Use plain HTML only: no markdown `**`, no LaTeX, no "sq" or "squares" for size. Give sizes in feet ("about 6 ft × 4 ft").

### 2.1 What each tier is, and what it adds

| | GOOD | BETTER = everything in GOOD, plus | BEST = everything in BETTER, plus |
|---|---|---|---|
| Area | the targeted leak area (one per numbered area) | **expanded area** around the leak (the full valley run, the full wall line) | **2x / double-wide area** or the full roof section |
| Underlayment | commercial-grade waterproof underlayment | **high-performance, high-temperature self-adhered** (peel & stick) | **double underlayment**: two full self-adhered layers |
| Deck | inspect the deck ("Underlayment & deck inspection") | **plywood decking replacement** (allowance: "up to 1 sheet 4x8 CDX" or "as found") | plywood across the whole 2x footprint |
| Seals and flashing | flashing and penetration reseal **in the repair zone**; the wall or transition joint if it is the leak | double sealing of seams, flashing, transition | all penetrations, vents and boots resealed; multi-layer elastomeric; **roof maintenance / tune-up** |
| Materials | reinstall the original tiles; matching replacements for cracked pieces | matching replacement tiles as needed | tile bonding / structural reinstall |
| Close-out | cleanup + magnetic sweep + final inspection | same | **photo documentation** of the completed work |
| Warranty | **1 month** (default) | **1 month** (default) | **1 month** (default) |
| Usual extra findings | — | fascia board, hip re-mortar of the adjacent run, pipe boots, flat-junction recoat | full valley system, skylight or chimney re-flash, full flat recoat |

Within the 35 three-tier proposals, BETTER most often added: plywood (25), expanded area (24), premium
underlayment (17). BEST most often added: tune-up (21), full section (16), photo documentation (15), double
underlayment (14), penetration reseal (13), 2x area (11).

### 2.2 Canonical step titles and wording (reuse them; adapt the place and material)

**GOOD** (Tile, 1-story; DTR-129 / 131 / 108 style):
1. **Diagnostic Prep & Access** (or **2-Story Access & Safety Rigging** when the photos show 2 stories): "Set up safe access to the roof. Perform a focused inspection over the <room> leak area."
2. **Tile Removal & Substrate Inspection**: "Carefully remove surrounding concrete roof tiles at <the exact spot> to inspect the condition of the underlying underlayment and decking."
3. **Underlayment & Waterproof Sealing**: "Remove degraded underlayment, install fresh commercial-grade waterproof underlayment, and seal all perimeter seams and transition joints with heavy-duty elastomeric/bull membrane sealant."
4. **Flashing & Penetration Reseal** (only the joints that exist in the photos): "Inspect and reseal adjacent wall flashings, transition metal seams, and roof penetrations within the repair zone."
5. **Tile Re-Integration**: "Reinstall original concrete tiles securely over the repaired section, supplying matching replacement tiles for any cracked or broken pieces."
6. **Site Cleanup & Final Inspection**: "Perform full job site cleanup, grounds magnetic sweep for metal fasteners, and execute a final quality inspection."

**BETTER** (DTR-129 / 131):
1. **Expanded Sector Inspection & Teardown**: "Everything in the Good Option, extended across a wider tile removal perimeter around <the spot> to capture hidden water migration."
2. **Plywood Decking Inspection & Replacement**: "Evaluate structural wood decking upon material removal. Remove rotted or water-damaged deck sections and install new code-compliant exterior-grade CDX plywood decking (up to 1 sheet 4x8) across the expanded repair footprint."
3. **Better Underlayment Upgrade**: "Install premium high-performance, high-temperature self-adhered waterproof underlayment across the expanded sector, securely lapped into surrounding roof sections."
4. **<Transition / Valley / Wall> Flashing & Double Sealing**: "Apply a double layer of heavy-duty elastomeric waterproofing sealant over all metal seams, wall flashings and perimeter tile overlaps."
5. **Tile Realignment & Reinstallation**: "Re-align, interlock, and mechanically reinstall concrete roof tiles across the expanded area, supplying matching replacement tiles as needed."
6. **Site Cleanup & Magnetic Sweep**: "Perform full job site cleanup, grounds magnetic sweep for loose fasteners, and conduct a final quality inspection."

**BEST** (DTR-129 / 131 / 135):
1. **2x Extended Teardown & Substrate Access**: "Everything in the Better Option, covering a double-wide work area across <the zone> and surrounding high-risk roof sectors to eliminate hidden water migration."
2. **Plywood Decking Replacement**: "Remove all damaged, warped, or rotted wood decking across the 2x expanded footprint and install fresh, code-compliant exterior-grade CDX plywood decking."
3. **Double Underlayment Protection System**: "Install two full layers of high-performance, high-temperature self-adhered waterproof underlayment across the entire expanded footprint for maximum multi-layer defense."
4. **Premium Sealing & Overall Roof Maintenance**: "Apply multi-layer elastomeric waterproofing compound over all transition seams, flashing joints, and overlaps while resealing adjacent roof penetrations, vents, and boots in the work zone."
5. **Tile Structural Repair & Reinstallation**: "Mechanically reinstall surface materials across the expanded perimeter, supplying matching replacements as needed."
6. **Site Cleanup & Photo Documentation**: "Execute meticulous job site cleanup, grounds magnetic sweep, and deliver a photo-documented quality sign-off."

**Material variants:**
- **Shingle** (DTR-135): "Shingle Removal & … Teardown: carefully remove approximately N architectural/dimensional shingles …".
  Then "Shingle Re-Integration & Perimeter Sealing: install **new matching** dimensional shingles (up to 1 bundle /
  ~10 shingles) …". Shingles are replaced, not "reinstalled set-aside materials". Board or T&G decking → "T&G
  Decking Repair / Plywood Replacement".
- **Flat** (lessons from jobs 269, 683, 699, 757): name the **base sheet + granulated modified-bitumen cap sheet**
  (or the membrane you see), never "underlayment" or "tiles". GOOD = cut out the failed area, new base sheet + cap
  sheet patch about <size>, mastic border, granules, seal the penetrations in the patch. BETTER = expanded section or
  the full junction line, membrane lapped up the curb/wall, recoat the junction area. BEST = full-section re-cover or a
  full elastomeric recoat of the flat roof. Re-cover vs tear-off is a valid 2-tier split (DTR-48). Write the method
  the crew will use (torch-applied vs self-adhered).
- **Metal** (job 739): a penetration leak is "reseal/replace pipe boot at penetration", spot-size, GOOD. No
  underlayment lines.

`option_description` patterns (most frequent):
- GOOD "Targeted <Location> <Material> Leak Repair Package", for example "Targeted Entrance Leak & 4ft Valley Flashing Repair Package".
- BETTER "Plywood Decking & Better Underlayment Repair Package" (+ ", Expanded Area" / "& Garage Valley Repair").
- BEST "Double Underlayment, Plywood Decking Replacement & 2x Expanded Area Waterproofing Package (Full Coverage)".

Option names are exactly `GOOD`, `BETTER`, `BEST`. Exactly one option is `is_recommended`. It is BEST by convention
(42 of 42 three-tier proposals). Make BETTER the recommended one instead when the photos make BETTER the minimum
correct fix (visible rot, two leak zones, curb lap needed), and say so to Owen.

### 2.3 Warranty — DEFAULT 1 MONTH ON EVERY OPTION (owner, 2026-11-09)

Every option (Good, Better and Best) gets **1 month** of workmanship warranty unless Owen gives another period for
this proposal. Text: `Workmanship Warranty: 1 Month This warranty covers workmanship on <the work in THIS option>
for 1 month from completion.` Leaving `warranty` out gives that default. The history below is for reference only.

#### History (do not use as the default)

| Tier | Text (adapt the "covers" list so it names only work that is in THIS option's scope) |
|---|---|
| GOOD | `Workmanship Warranty: 3 Months This warranty covers workmanship on targeted primary leak repair, underlayment tie-in, and material reinstallation for 3 months from completion.` |
| BETTER | `Workmanship Warranty: 6 Months This warranty covers workmanship on expanded transition leak remediation, plywood replacement, better underlayment installation, seam waterproofing, and tile reinstallation for 6 months from completion.` |
| BEST | `Workmanship Warranty: 1 Year This warranty covers workmanship on 2x expanded repair, double underlayment installation, plywood replacement, transition sealing, and full material reinstallation for 1 full year from completion.` |

Until Nov 9 the most common values were 3 months / 6 months / 1 year; the owner replaced them with 1 month on every option. Older variants exist, and you
should NOT copy them: 30 days on GOOD and BETTER (DTR-34, 38, 40, 97, 135, 137), 90 days on all three tiers
(DTR-22, 24), 180 days, 9 months (DTR-74), and BETTER at 3 months (DTR-136). A $0 GOOD also gets 3 months unless
Owen says 30 days (open question).

### 2.4 Payment terms (exact; amounts = price ÷ 2, two decimals)

- AHS job, price > 0: `Payment Terms: 50% Upfront ($750.00) – due upon acceptance of proposal. 50% Upon Completion ($750.00) – due after final inspection and customer approval. (AHS portion billed directly to AHS).`
  Luis's text says "billed directly to insurance". AHS is a home-warranty company, not insurance. Write "AHS" and
  tell Owen once (open question 9 in the analysis).
- Retail job (no AHS): the same text without the parenthesis.
- $0 option: `Payment Terms: No out-of-pocket payment – this option is covered by your AHS authorization.` Do not
  write "50% Upfront ($0.00)" (job 721 lesson).
- `deposit` = exactly 50% of `price` (0 when the price is 0). History broke this 5 times (DTR-114, 120, 133 at 100%;
  DTR-104; DTR-39).

---

## 3. The price block

```
(Retail Value: $R | Special AHS Member Price: $M | AHS Authorized Credit: -$C | Customer Pays Out of Pocket: $P | Total Savings: $S)
```

| Figure | How to get it | Rule |
|---|---|---|
| **P**, customer pays | Owen's price, else the estimate (§4) | **P = the option's `price`.** It broke 11 times in history (DTR-29, 36, 37, 46, 50, 58, 116 …). |
| **C**, AHS credit | **from the job**, never assumed (below) | the same C in all three options |
| **M**, member price | M = P + C | true in 114 of 114 historical rows: the only invariant |
| **R**, retail value | the anchor by tier (below) | must be > M; otherwise use the next anchor up |
| **S**, savings | S = R − P | the dominant formula (132 of 159). Do not use R − M or "S = C". |

**Retail anchors** (late era, DTR-115 to 138): GOOD **$2,500**, raised to $2,800 / $3,200 when larger, 2-story or
two areas. BETTER **$4,500**, raised to $4,800 / $5,000 / $5,300. BEST **$8,500**, raised to $8,800. R is a marketing
anchor, not a cost. Never let M exceed R (DTR-116: M $2,625 > R $2,500).

**Where C comes from. In this order; if none of them exists, ASK Owen. Never guess $1,125.**
1. Owen says it in the chat.
2. **Frontdoor, AHS's own portal** (2026-11-12): `frontdoor_dispatch` with the job's `AHS Job ID` field →
   `authorization.authorized_to_contractor` is C, and `customer_owes` / `total` are AHS's own estimate (say so if
   you quote them). `requested: false` = AHS has not authorized anything yet → ask Owen. If the tool answers
   "Frontdoor reading is off", go on to 3.
3. The authorization on the job: a note or Dispatch item "AHS approved #<n>: $<net>" / "AUTHO #" / "NCC $… Net
   Total $…", or the job field `AHS authorized ($)`. If you only find an NCC/Net Total note, show both figures to
   Owen and ask which one is the credit.
4. The job's `AHS-LEAK-REPAIR` line price (57 of 65 matched the text). `AHS-TRIP` / `AHS-TILE-01` ($100 trip or
   inspection) are NOT part of the credit. How `AHS-ADDL-LEAK` adds to the credit is not known, so ask.

Historical values: −$1,125 (90 rows), −$1,100 (51), −$1,000 (6, e.g. DTR-119, 129, 138), and odd −$600 / −$100 /
−$25. Each one was job-specific. A value outside $1,000–1,125 → put it in `notes_for_owen`.

**Retail (non-AHS) jobs** have no credit and no member price. Use
`(Retail Value: $R | Dream Team Discount: -$D | Customer Pays Out of Pocket: $P | Total Savings: $D)` with R − D = P
(DTR-48 pattern), or ask Owen. Never put an AHS figure on a retail job (DTR-48 "Total Savings $1,125").

**$0 GOOD is right only when** the job is AHS, C is known, and AHS covers the whole GOOD scope: one leak area,
standard underlayment, the existing tiles or shingles reset, no plywood, no flashing replacement, no 2-story
surcharge. Then P = 0, M = C, R = $2,500, S = $2,500. 38 of 95 GOODs were $0, including the whole Oct 15-19 batch.
As soon as GOOD needs more than that (a wall or transition flashing joint, replacement tiles, 2-story access, a
second area, rot), GOOD is paid (§4). A $0 GOOD is never a placeholder for a scope nobody wrote (DTR-39, 57, 59).

---

## 4. Price estimator (use only when Owen gave no price)

**This is an ESTIMATE from history, and Owen may override it.** It is the customer-pays figure after the AHS
credit. It was computed from the late era (created Oct 14 on, mainly DTR-115 to 138, where Tile BEST is $3,000 in 9
of 16). Earlier prices were lower (GOOD med $375, BETTER $980, BEST $2,000). Customers almost always took GOOD, and
no customer ever chose a priced BEST over a priced GOOD/BETTER.

### 4.1 Base table: customer pays by template × tier × situation

| Situation (from the photos) | GOOD | BETTER | BEST | Comparables (G / B / B customer pays) |
|---|---|---|---|---|
| Tile, 1 leak, 1-story, AHS covers the targeted repair | **$0** | $1,100–1,400 | **$3,000** | DTR-135 0/1,100/3,000 (shingle vent); DTR-132, 137 ($0 GOOD) |
| Tile, 1 leak, 1-story, GOOD needs a wall, transition or flashing joint, or replacement tiles | **$375–600** (std $375; $500–825 for transition packages) | **$1,200–1,400** | **$3,000** | DTR-129 600/1,400/3,000; DTR-130 350/1,200/3,000; DTR-134 600/1,250/3,000; DTR-72 575/1,325/3,000; DTR-67 700/1,450/3,000; DTR-133 375, 115 825 |
| Tile, 2-story, 1 leak | **$650–750** | **$1,475–1,500** | **$3,000** | DTR-131 660/1,500/3,000 (4 ft → full 8 ft valley); DTR-136 750/1,485/3,000; DTR-74 GOOD 450 |
| Two leak areas: GOOD = one area, BETTER = both | $300–675 | **$1,400–2,175** | **$4,175–5,000** | DTR-117 300/1,400/5,000; DTR-127 675/2,175/4,175 (+ add-on $750) |
| GOOD itself must cover 2+ areas | **$1,000–1,550** | — | — | DTR-125 1,000; DTR-116 1,560; DTR-126 1,485 (retail, after a same-day discount) |
| Shingle (Custom template), 1 leak | $0 / $275–450 | ~$1,100 | ~$3,000 | DTR-135 0/1,100/3,000; DTR-60 450; DTR-43/47/49 275 (early). Thin data: say so. |
| Shingle + flat, flat section failing | $1,200 patch | $3,800 flat re-cover | $5,500 re-cover + full coating | DTR-128 (no AHS block) |
| Flat (AHS), 1 area | $300–750 | $750–1,485 | $2,000–3,000 | DTR-136 750/1,485/3,000 (2-story); DTR-81 BEST 1,912.50 (3 zones) |
| Flat, retail, per leak area | $1,000 per leak (list) | — | — | DTR-124/126 "Standard Rate (2 Leaks): $2,000 ($1,000 per leak)" |
| Flat small section, retail: re-cover vs tear-off (17 ft × 21 ft) | — | $3,000 re-cover | $7,500 tear-off + new | DTR-48 |
| Tile or shingle leak, retail (no AHS) | $1,500–2,000 | $1,500–2,000 | no data → ask | DTR-121/122 GOOD 2,000; DTR-89 BETTER 1,500; DTR-101 BETTER 2,000 |
| Replacement | ask Owen | ask Owen | ask Owen | 1 example only |

### 4.2 Adders (on top of the base row)

| Driver seen in photos | Add | Evidence |
|---|---|---|
| 2nd story (access and rigging) | GOOD +$250–375 · BETTER +$0–100 · BEST +$0 | 375 → 660/750; 1,400 → 1,485/1,500; BEST stays 3,000 |
| Each extra leak area inside the tier | +$700–1,000 per area (the only stated rate is $1,000 per leak) | DTR-126/124; DTR-117, 127 |
| Plywood in GOOD (rot is the leak cause) | +$500 per 4×8 sheet | catalog L024 $500; DTR-27 "$500 Add-On Included" ($1,375, 2-story) |
| Plywood beyond the allowance | +$500 per sheet (the text range is $500–750) | DTR-127, 41, 44 |
| Plywood in BETTER / BEST | included ("up to 1 sheet", "across the 2x footprint") | DTR-129, 131, 135 |
| Full valley run instead of a section | inside BETTER (~$1,500) | DTR-131 |
| Fascia board at the eave or rake | inside BETTER (~$1,200) | DTR-130 |
| Up to 10 replacement tiles as an add-on | $750 (~$75 per tile) | DTR-123 |
| Flat puncture patch | +$350 | DTR-62 |
| Card fee | never inside the price | job 288 |

### 4.3 Steps from findings to a number

1. Owen gave prices → use them, skip to step 7.
2. Pick the base row from the template, story count and number of **distinct** leak areas (§5).
3. Pick the point in the range: low end for a small, clean single spot; high end for wall, valley or transition
   work, visible rot or a hard access.
4. Add the adders (§4.2) only for findings a photo shows.
5. Round: under $1,000 to the nearest **$25**; $1,000 and up to the nearest **$50**. BEST defaults to **$3,000**
   for a single area.
6. Check the ladder: GOOD < BETTER < BEST. BETTER − GOOD ≥ $500 (late median ~$800). BEST − BETTER ≥ $1,000 (late
   median ~$1,600). Never a $10 step like DTR-71 and DTR-74 (BETTER $1,490 / BEST $1,500).
7. Build the price block (§3), deposit = 50%, payment amounts = P/2.
8. **Show Owen the basis**: the row used, each adder, and 2-4 comparables "DTR-N: G/B/B". Put the same text in
   `notes_for_owen`.

---

## 5. Photos → scope

### 5.1 Before anything: are there enough photos?

Pull `job_photos` (inspection set) and Read every photo you use. You need **a roof overview, a close-up of the leak
point, and the interior stain**.
- **No roof photo** (only interior, checks, street shots): do NOT draft a priced scope. Tell Owen
  "needs inspection photos" (jobs 308, 314, 316, 335, 388, 678, 703, 736). If he insists: GOOD only, "if found"
  wording, and the gap noted.
- Interior only, nothing above it: do not name a roof component ("garage valley") that no photo shows (job 736/738).
- Only repair-day photos (the work is already done): tell Owen. A proposal written after the fact must describe the
  work the photos show.
- Skip payment checks and screenshots, other houses, duplicates and blurry frames. Never use personal details from
  a check.
- **Count distinct objects:** frames seconds apart from the same spot show ONE thing. If unsure, write "several" or
  "up to N".

### 5.2 Finding → line → tier

| Visible finding | Line to write | Tier |
|---|---|---|
| One interior stain + one roof spot | targeted repair at that spot | GOOD |
| Inspector's numbered marks ("1", "2") or 2 interior stains or 2 tarps | one GOOD line per area; BETTER covers both | GOOD = the main area, BETTER = all areas (DTR-117, 127) |
| Rotted, soft, holed or delaminated deck; inspector caption "wood is bad"; long-standing mold below | plywood "up to N sheets 4x8 CDX" | at least BETTER (recommend it); inside GOOD (+$500/sheet) when it is the leak cause |
| Deck **stained but intact** | none, or "replace if found, $500 per sheet" | never a priced line |
| Tile against a stucco wall, open gap or old mastic at the wall line | **headwall/sidewall flashing**: counter-flashing or stucco stop, membrane turned up the wall | GOOD when it is the leak. It is NOT a "flat roof transition". |
| Rusted or perforated metal flashing at a wall or buttress | replace corroded flashing / pan at the wall junction | GOOD if it is the leak source |
| Tile slope meeting a flat roof (bottom tile course) | lift the bottom course, new base/cap at the junction, reset tile | GOOD |
| Split or rusted valley metal, shattered valley tiles | replace the valley metal section; "replace N broken valley tiles" | GOOD (section) / BETTER (the full run, at the inspector's measured length) |
| Debris packed in a valley | clear valley debris | the tier that fixes the valley |
| Lower roof valley dead-ending under an upper eave or fascia | diverter / kick-out or counter-flashing | GOOD |
| Crumbling hip or ridge mortar, broken hip piece | re-mortar / re-set the hip caps on the affected run | BETTER or BEST (or add-on). Worn mortar with caps in place = note only. |
| Cracked field tiles away from the leak | "replace up to N cracked tiles" (N = distinct count) | GOOD near the leak; a larger count in BETTER |
| Clay tile | "source matching clay tiles" | every tier that lifts tiles |
| Deteriorated lead stacks or pipe boots | replace pipe boots | BETTER (GOOD if at the leak) |
| Rusted turbine or gooseneck vent | replace vent + flashing (not just "reseal") | BETTER/BEST |
| Single penetration with an old cement patch | reflash / replace that vent, about 8 shingles | GOOD (DTR-135) |
| Cracked mastic around a solar fan or AP vent | reseal | GOOD |
| Skylight ringed with mastic | skylight re-flash (needs a curb close-up) | BEST at least |
| Chimney cricket with roof cement or plants | chimney re-flash / cricket rebuild | BETTER/BEST |
| Flat: open or bubbled cap-sheet seam | cut out + base + cap patch, mastic border, granules | GOOD |
| Flat: worn coating, ponding across the roof | recoat the junction area (BETTER); full recoat or re-cover (BEST) | BETTER/BEST |
| Ponding or rust ring at a curb + downspout dumping there | redirect/extend the downspout; lap new membrane up the curb | GOOD/BETTER |
| Standing water in an opened flat area | replace the wet base sheet / insulation in the opened area | the tier that opens it |
| Rotted fascia or rake board | replace N ft of fascia | BETTER (DTR-130) or add-on |
| Rotted rafter or truss end | its own framing line, or "not included, priced separately if found" | never hidden in another line |
| Torn or rusted drip edge | edge-metal replacement | BETTER or add-on |
| Tile or shingle meeting a metal patio roof | transition / L-flashing | GOOD if leaking, else BETTER |
| Palm fronds or branches on the roof | trim back vegetation | note or add-on |
| Clogged gutter **shown** | gutter clearing | only if a photo shows it |
| 2-story | "2-Story Access & Safety Rigging" step | every tier |
| Water at a window base, disconnected exhaust duct, AC line | NOT roof scope: flag it as a possible non-roof source | `notes_for_owen` |
| Interior ceiling, drywall or mold | "Interior repairs not included" | note |

### 5.3 Coverage rule

**Every damage item you saw must end up either in a tier's scope or in `notes_for_owen`** as "seen, not priced
(photos N, M)". The audits found unaddressed damage on most jobs: hip mortar (jobs 284, 687, 690, 710, 728, 730,
754), rot that was replaced but never priced (368, 389, 627, 656, 695, 745), worn flat coating (269, 288, 682, 741),
fascia (294, 627, 711, 730). Rot found on repair day with only GOOD sold means a change order. Say so.

---

## 6. Never (each item happened in a real proposal)

- **Copy-paste location:** "(Kitchen light area)" / "active kitchen leak" was pasted into 35 options on 33 jobs.
  Room names come from THIS job's notes, chats or photos. The option title and every line use the same room
  (DTR-127: titled "Bathroom", scoped "living room").
- **Placeholders:** "(state warranty)", "(write the scope for this job)", "Custom Package (write scope and price on
  site)" as the only text, or a $0 unfilled GOOD/BETTER (DTR-39, 57, 59-66). Fill all three or leave a tier out.
- **Price-block mismatch:** customer pays ≠ option price, M ≠ P + C, payment halves ≠ P/2 (DTR-29, 37, 43, 50, 58, 116).
- **Deposit ≠ 50%.**
- **Flat wording with no flat roof** ("where the pitched tile roof meets the flat roof" on a tile-to-wall leak, DTR-129, job 294).
  **Tile wording on shingle/flat** ("reinstall concrete tiles", DTR-89, job 269).
- **Unpriced work promised:** never put a fix in a scope (or in the inspection report) that no option prices.
  Never imply that framing, fascia or interior repair is included when it is not.
- **Components not seen:** roof drain, TPO, "undersized vent", skylight, chimney, gutters, "two-story", roof age:
  write them only when a photo shows them.
- **Tier label vs content:** no "Everything in the Better Option" in GOOD (DTR-104). No "Everything in the Good
  Option" when there is no GOOD (job 681). No BETTER text in the GOOD slot (DTR-107).
- **Warranty covers only this option's work:** the period is 1 month by default (owner) and the text names only work in that option (DTR-16
  BEST covered "ponding area restoration" that was not in its scope).
- **Weak ladder:** BEST $10 above BETTER (DTR-71, 74). Different prices for identical scope on the same job.
  Different AHS credits across tiers or versions (DTR-38 vs 40).
- **$0 GOOD when AHS does not cover the whole GOOD scope**, or when C is unknown.
- **"AHS approved":** write "AHS authorized" (the company wording rule). AHS is not "insurance".
- **Customer personal data** in `notes_for_owen` beyond what Owen needs; never from a payment check.

---

## 7. Title

Use the most common recent pattern (51 proposals, Oct 14 on): **`Active Leak Repair - <service street address>`**
(the address from the job). Other patterns seen: `<Customer Name> - <8-digit AHS dispatch #> ROOF` (22, AHS jobs,
matches the work-order naming), and the Zuper default `Proposal for <CUSTOMER NAME>`. Never a title that does not
match the scope (job 681: "New Gutters" on a leak repair).

---

## 8. The auto-created template draft (CPQ column triggers)

The team wired the templates to board columns (`cpq_config.trigger`, as checked 2026-11-07): **Flat, Tile and Custom
on AHS - Inspection / Proposal Made**, **Roof Replacement on AHS Simple Flow / Proposal Creation**. Older notes say
"Simple Flow has no trigger"; they are outdated. When a job enters one of those columns, Zuper may create a DRAFT
proposal from the template by itself (created by Zuper, `cpq_status.proposal_generation_type: "MERGE"`, for example
DTR-150), with the template's placeholder lines and prices (Tile $0 / $0 / $2,000; Flat $1,500 / $1,900 / $3,000,
plus any checklist products merged into the LOOKUP line). What Zuper does when 3 templates fire on one column is not
known.

- **`propose_proposal_draft` detects such an auto-created DRAFT on the job and fills it** (on approval) instead of
  making a second proposal. You do not create, delete or edit it yourself.
- In the dossier, look at the job's proposals before drafting. Tell Owen if:
  - an auto draft exists with a **different template** than the roof needs (put it in `notes_for_owen`); or
  - a proposal is already **sent or approved** (AWAIT_RESPONSE, AWAIT_SIGNATURE, APPROVED). Do not draft another
    one unless Owen asks for a reissue, then set `allow_second: true`.
- `allow_second` stays false/absent otherwise.
- After approval, `zuper_proposals` attaches `Proposal-DTR-N.pdf` to the Zuper draft 1-2 minutes later. That is
  expected.

---

## 9. Checklist before calling `propose_proposal_draft`

- [ ] Prices are Owen's, or estimates with the basis (row, adders, DTR comparables) written in `notes_for_owen`.
- [ ] I read every photo I relied on; I have an overview, a leak close-up and the interior (or I told Owen they are missing).
- [ ] Template matches the roof **at the leak** (no `tile` on shingle, flat or metal).
- [ ] Material named right (clay vs concrete, mod-bit vs TPO, shingle wording).
- [ ] Counts come from distinct objects; sizes only if measured, in feet.
- [ ] Room or area names are this job's, the same in the title, option description and every line.
- [ ] Each tier = "everything in the previous, plus …"; 5-8 steps; no placeholders.
- [ ] Every damage item is in a tier or in `notes_for_owen` ("seen, not priced").
- [ ] Price block: P = price; M = P + C; S = R − P; R > M; the same C in every option; C came from the job or Owen.
- [ ] $0 GOOD only when AHS covers the whole GOOD scope.
- [ ] Ladder: BETTER − GOOD ≥ $500, BEST − BETTER ≥ $1,000; rounded to $25 / $50.
- [ ] Warranty 1 month on every option (unless Owen gave another), covering only this option's work.
- [ ] `deposit` = 50%; payment halves = P/2 with two decimals; "billed directly to AHS"; $0 text on a $0 option.
- [ ] Exactly one `is_recommended`.
- [ ] Title follows §7.
- [ ] Checked the existing proposals (auto draft? already sent?) and set `allow_second` only when Owen asked.
