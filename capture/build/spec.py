"""The checklist spec: what Dream Team Roofing actually fills in today.

Source of truth is Owen's own CompanyCam and Workiz records (2026-08-13), NOT the
ChatGPT draft that opened this task. The difference is the whole point:

* The draft proposed 23 "Customer Journey / Technician Journey" milestone tickboxes.
  Nobody on the team fills those in.
* What they really use is (a) an inspection QUESTIONNAIRE with answers - "How many
  leaks? -> 1", "27 broken tiles", "Roof material? -> Tiles" - which is data capture,
  not tickboxes, and (b) a 14-shot PHOTO checklist, which genuinely is tickboxes.
* Workiz's "Extra Info" tab duplicates three of those questions (`How old is the
  roof`, `What is the Roof Type?`, `Notes and Updates`). Merged, not copied twice.
  Only its two rollups are new.

Workiz is being retired (Owen, 2026-08-13), so the 12 legacy `workiz_*` opportunity
fields are left in place but nothing here depends on them.

GHL's CHECKBOX is a multi-select group. For a photo checklist that is exactly right:
all 14 shots are ONE field with 14 options, not 14 fields.
"""

OBJECT = "Opportunity"

# The 14 photo shots, deduped - Owen's list repeats "Left & Right Side Elevations".
# Section names are kept in the option text so the grouping survives the flattening
# into a single multi-select.
PHOTO_ITEMS = [
    "Front Elevation (Full View)",
    "Rear Elevation (Full View)",
    "Left & Right Side Elevations",
    "Street View / Driveway Approach",
    "Driveway Surface (Full Length)",
    "Driveway Close-Ups",
    "Walkways & Patios",
    "Windows & Exterior Openings",
    "Vents, Louvers & Utility Openings",
    "Immediate Perimeter (Landscape)",
    "Soffit/Fascia - Continuous Run",
    "Soffit/Fascia - Corner Details",
    "Soffit/Fascia - Ventilation",
    "Soffit/Fascia - Material & Damage",
]

FOLDERS = [
    "Roof Inspection",
    "Photo Checklist",
    "Customer Journey Checklist",
]

# (folder, name, type, [options], description)
# `type` strings are the MEASURED labels from GHL's own Field type dropdown:
#   Single line, Multi line, Text box list, Number, Phone, Monetary,
#   Dropdown (single), Dropdown (multiple), Radio select, Checkbox
FIELDS = [
    # --- Roof Inspection: the CompanyCam questionnaire, plus Workiz Extra Info ---
    ("Roof Inspection", "How Many Leaks", "Number", [],
     "Number of active leaks reported or found."),
    ("Roof Inspection", "Roof Age (Years)", "Number", [],
     "Approximate age of the roof in years. (Workiz: 'How old is the roof')"),
    ("Roof Inspection", "Roof Material", "Dropdown (single)",
     ["Tiles", "Shingles", "Metal", "Flat / Modified Bitumen", "Other"],
     "Roof covering type. (Workiz: 'What is the Roof Type?')"),
    ("Roof Inspection", "How Many Stories", "Number", [],
     "Number of storeys - drives ladder and safety requirements."),
    ("Roof Inspection", "Wood Condition", "Dropdown (single)",
     ["Good", "Damaged", "Can't Tell"],
     "Condition of decking/wood where visible. 'Can't Tell' is a real answer."),
    ("Roof Inspection", "Previous Repair", "Dropdown (single)",
     ["Yes", "No", "Unknown"],
     "Has the affected area been repaired before?"),
    ("Roof Inspection", "Leak Location (Exterior)", "Single line", [],
     "Where on the roof the leak originates, e.g. 'Garage'."),
    ("Roof Inspection", "Leak Location (Interior)", "Single line", [],
     "Where inside the property the leak shows."),
    ("Roof Inspection", "Broken Tiles / Missing Shingles", "Single line", [],
     "Free text, not a count - real answers look like '27 broken tiles'."),
    ("Roof Inspection", "Inspection Notes", "Multi line", [],
     "Scope and findings. (Workiz: 'Notes and Updates')"),

    # --- Photo Checklist ---
    ("Photo Checklist", "Photos Captured", "Checkbox", PHOTO_ITEMS,
     "Tick each shot as it is taken. 14 items across 5 sections."),
    ("Photo Checklist", "Enough Photos", "Checkbox", ["Done"],
     "Rollup. (Workiz: 'Does we got enough Pictures?')"),
    ("Photo Checklist", "Checklist Complete", "Checkbox", ["Done"],
     "Rollup. (Workiz: 'Does Checklist are done?')"),

    # --- Customer Journey milestones (Owen, 2026-08-13: include these too) ---
    ("Customer Journey Checklist", "Customer Contacted", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Appointment Scheduled", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Appointment Confirmed", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Technician Assigned", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Estimate Sent", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Estimate Approved", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Deposit Collected", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Work Scheduled", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Work Completed", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Final Payment Collected", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Review Requested", "Checkbox", ["Done"], ""),
    ("Customer Journey Checklist", "Job Closed", "Checkbox", ["Done"], ""),
]


def summary():
    by_folder = {}
    for folder, name, ftype, opts, _ in FIELDS:
        by_folder.setdefault(folder, []).append((name, ftype, len(opts)))
    return by_folder


if __name__ == "__main__":
    total = 0
    for folder, rows in summary().items():
        print("\n=== %s (%d) ===" % (folder, len(rows)))
        for name, ftype, nopts in rows:
            total += 1
            extra = (" [%d options]" % nopts) if nopts else ""
            print("  %-34s %-20s%s" % (name, ftype, extra))
    print("\n%d fields across %d folders" % (total, len(FOLDERS)))
