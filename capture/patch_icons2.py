"""Add the per-component icons the component diff found missing.

Each is an icon GHL renders that we did not. Counts came from
capture/compare_components.py after its own icon-detection bug was fixed
(SVG tagName is lowercase; uppercase comparison scored every SVG as "not an icon").
"""
import pathlib

FE = pathlib.Path("frontend/src")


def patch(path, pairs, imports=None):
    p = FE / path
    s = p.read_text(encoding="utf-8")
    n = 0
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            n += 1
        else:
            print("  MISS %s: %s" % (path, old.strip().splitlines()[0][:58]))
    if imports and imports.strip() not in s:
        i = s.index("\n", s.rindex("import ", 0, s.index("\n\n")))
        s = s[:i + 1] + imports + s[i + 1:]
    p.write_text(s, encoding="utf-8")
    print("%s: %d/%d" % (path, n, len(pairs)))


# stage header: GHL has a collapse-stage chevron (1 icon)
patch("pages/OpportunitiesPage.tsx", [
    ("""        <div
          className="truncate"
          title={stage.name}
          style={{ fontSize: 14, fontWeight: 700, color: 'rgb(16,24,40)' }}
        >
          {stage.name}
        </div>""",
     """        <div className="flex items-center gap-1">
          <div
            className="min-w-0 flex-1 truncate"
            title={stage.name}
            style={{ fontSize: 14, fontWeight: 700, color: 'rgb(16,24,40)' }}
          >
            {stage.name}
          </div>
          {/* measured: GHL renders a "Collapse stage" chevron in each header */}
          <button title="Collapse stage" aria-label="Collapse stage"
            style={{ color: 'rgb(102,112,133)' }}>
            <IconChevronLeft size={16} color="rgb(102,112,133)" />
          </button>
        </div>"""),
    # advanced-filters icon on the status control
    ("""        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}""",
     """        <IconFilter size={16} color="rgb(0,78,235)" />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}"""),
], imports="import { IconChevronLeft, IconFilter } from '../components/Icon'\n")


# contacts: sort caret per column header (GHL shows one on the sortable column)
patch("pages/ContactsPage.tsx", [
    ("""                  >
                    {c.label}
                  </th>""",
     """                  >
                    <span className="flex items-center gap-1">
                      {c.label}
                      {/* measured: GHL renders a sort caret in the column header */}
                      <IconChevronDown
                        size={14}
                        color={sort === c.key ? 'rgb(0,78,235)' : 'rgb(152,162,179)'}
                      />
                    </span>
                  </th>"""),
], imports="import { IconChevronDown } from '../components/Icon'\n")


# calendars: chevron icons on filter group headers (were text "+"/"-")
patch("pages/CalendarsPage.tsx", [
    ("""        <span>{open ? '-' : '+'}</span>""",
     """        <IconChevronDown
          size={14}
          color="rgb(102,112,133)"
          className={open ? undefined : '-rotate-90'}
        />"""),
], imports="import { IconChevronDown } from '../components/Icon'\n")


# dashboard: each card has a caret on its pipeline select plus a settings icon
patch("pages/DashboardPage.tsx", [
    ("""        <div className="ml-auto flex items-center gap-2">{right}</div>""",
     """        <div className="ml-auto flex items-center gap-2">
          {right}
          {/* measured: every card carries a settings icon to the right of its select */}
          <button title="Card settings (not implemented in v1)"
            style={{ color: 'rgb(102,112,133)', cursor: 'not-allowed' }} disabled>
            <IconSettings size={18} color="rgb(102,112,133)" />
          </button>
        </div>"""),
], imports="import { IconSettings } from '../components/Icon'\n")
