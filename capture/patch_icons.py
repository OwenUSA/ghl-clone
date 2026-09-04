"""Apply measured icon + typography corrections to Contacts / Opportunities / Calendars.

From capture/spec.py (1440x900). Each replacement below is a measured value that
the first build got wrong — mostly weight, colour, and missing icons.
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
            print("  MISS in %s: %s" % (path, old.strip().splitlines()[0][:60]))
    if imports and imports not in s:
        first = s.index("\n", s.index("import "))
        s = s[:first + 1] + imports + s[first + 1:]
    p.write_text(s, encoding="utf-8")
    print("%s: %d/%d applied" % (path, n, len(pairs)))


# ---------------- Contacts ----------------
patch("pages/ContactsPage.tsx", [
    # Import: measured 13px/500 rgb(0,78,235) + 16x16 icon, NOT a grey bordered button
    ("""            height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: 'rgb(52,64,84)',
              border: '1px solid rgb(234,236,240)', opacity: 0.5,
              cursor: 'not-allowed',
            }}
          >
            Import
          </button>""",
     """            height: 36, padding: '0 10px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: 'rgb(0,78,235)', opacity: 0.5,
              cursor: 'not-allowed', display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <IconDownload size={16} color="rgb(0,78,235)" />
            Import
          </button>"""),
    # Add Contact: measured 13px/500 white + 16x16 icon
    ("""              height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
            }}
          >
            Add Contact
          </button>""",
     """              height: 36, padding: '0 12px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <IconPlus size={16} color="#fff" />
            Add Contact
          </button>"""),
    # smart-list row: measured 14px/400 rgb(102,112,133) + 16x16 icons, no blue underline
    ("""        <div
          style={{
            fontSize: 14, fontWeight: 500, color: 'rgb(0,78,235)',
            borderBottom: '2px solid rgb(0,78,235)', paddingBottom: 6,
          }}
        >
          All
        </div>""",
     """        <div className="flex items-center gap-2" style={{ paddingBottom: 6 }}>
          <IconList size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            All
          </span>
        </div>"""),
    ("""          <span style={{ fontSize: 14, fontWeight: 500, color: 'rgb(152,162,179)' }}>+</span>
          <span style={{ fontSize: 14, fontWeight: 500, color: 'rgb(152,162,179)' }}>
            Add Smart List
          </span>""",
     """          <IconPlus size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            Add Smart List
          </span>"""),
    # Filters / Sort: measured 14px/500 rgb(52,64,84) + 14x14 icons, no border
    ("""            height: 34, padding: '0 12px', borderRadius: 6, fontSize: 14,
            fontWeight: 500, color: 'rgb(71,84,103)',
            border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
          }}
        >
          Filters
        </button>""",
     """            height: 34, padding: '0 8px', borderRadius: 6, fontSize: 14,
            fontWeight: 500, color: 'rgb(52,64,84)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <IconFilter size={14} color="rgb(52,64,84)" />
          Filters
        </button>"""),
    ("""            height: 34, padding: '0 12px', borderRadius: 6, fontSize: 14,
            fontWeight: 500, color: 'rgb(71,84,103)',
            border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
          }}
        >
          Sort
        </button>""",
     """            height: 34, padding: '0 8px', borderRadius: 6, fontSize: 14,
            fontWeight: 500, color: 'rgb(52,64,84)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <IconSort size={14} color="rgb(52,64,84)" />
          Sort
        </button>"""),
    # Manage fields: measured 14px/600 rgb(71,84,103) + 20x20 icon
    ("""          style={{ fontSize: 14, fontWeight: 500, color: 'rgb(71,84,103)' }}
        >
          Manage fields
        </button>""",
     """          style={{
            fontSize: 14, fontWeight: 600, color: 'rgb(71,84,103)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <IconSettings size={20} color="rgb(71,84,103)" />
          Manage fields
        </button>"""),
], imports="import {\n  IconDownload, IconFilter, IconList, IconPlus, IconSettings, IconSort,\n} from '../components/Icon'\n")


# ---------------- Opportunities ----------------
patch("pages/OpportunitiesPage.tsx", [
    # pipeline name: measured 16px/400 rgb(52,64,84) as plain text + caret
    ("""            fontSize: 14,
            fontWeight: 500,
            color: 'rgb(16,24,40)',
            backgroundColor: '#fff',
          }}
        >""",
     """            fontSize: 16,
            fontWeight: 400,
            color: 'rgb(52,64,84)',
            backgroundColor: '#fff',
            border: 'none',
          }}
        >"""),
    # Board/List toggle -> measured 16x16 icons, active blue
    ("""                {v === 'board' ? 'Board' : 'List'}""",
     """                {v === 'board'
                  ? <IconGrid size={16} color={view === v ? 'rgb(0,78,235)' : 'rgb(96,113,121)'} />
                  : <IconList size={16} color={view === v ? 'rgb(0,78,235)' : 'rgb(96,113,121)'} />}"""),
    # Import + Add opportunity, measured 13px/500
    ("""        <button
          onClick={() => setShowFields(true)}
          className="ml-auto"
          style={{ fontSize: 13, fontWeight: 500, color: 'rgb(71,84,103)' }}
        >
          Manage fields
        </button>""",
     """        <button
          disabled
          title="Opportunity import is not implemented in v1"
          className="ml-auto flex items-center gap-1"
          style={{ fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)', opacity: 0.5, cursor: 'not-allowed' }}
        >
          <IconDownload size={16} color="rgb(0,78,235)" />
          Import
        </button>
        <button
          disabled
          title="Creating an opportunity is not implemented in v1"
          className="flex items-center gap-1"
          style={{
            height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13,
            fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
            opacity: 0.5, cursor: 'not-allowed',
          }}
        >
          <IconPlus size={16} color="#fff" />
          Add opportunity
        </button>
        <button
          onClick={() => setShowFields(true)}
          style={{ fontSize: 13, fontWeight: 500, color: 'rgb(71,84,103)' }}
        >
          Manage fields
        </button>"""),
    # saved-view tab row: measured "Open opportunities" + "List", 14px/400 with icons
    ("""      {/* toolbar */}
      <div className="flex shrink-0 items-center gap-2 px-4 pb-3">""",
     """      {/* saved views — measured 14px/400 rgb(102,112,133) with 16x16 icons */}
      <div className="flex shrink-0 items-center gap-6 px-4" style={{ height: 42 }}>
        <div className="flex items-center gap-2">
          <IconList size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            Open opportunities
          </span>
        </div>
        <div className="flex items-center gap-2" title="Saved views are not implemented in v1">
          <IconPlus size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            List
          </span>
        </div>
      </div>

      {/* toolbar */}
      <div className="flex shrink-0 items-center gap-2 px-4 pb-3">"""),
], imports="import { IconDownload, IconGrid, IconList, IconPlus } from '../components/Icon'\n")


# ---------------- Calendars ----------------
patch("pages/CalendarsPage.tsx", [
    # Today: measured 14px/600
    ("""            fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)',
          }}
        >
          Today
        </button>""",
     """            fontSize: 14, fontWeight: 600, color: 'rgb(52,64,84)',
          }}
        >
          Today
        </button>"""),
    # date range: measured 14px/600
    ("""          <div style={{ minWidth: 200, textAlign: 'center', fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' }}>""",
     """          <div style={{ minWidth: 200, textAlign: 'center', fontSize: 14, fontWeight: 600, color: 'rgb(52,64,84)' }}>"""),
    # view select: measured 14px/600
    ("""            height: 36, borderRadius: 6, border: '1px solid rgb(234,236,240)',
            padding: '0 10px', fontSize: 14, backgroundColor: '#fff',
          }}
        >
          {VIEWS.map((v) => <option key={v}>{v}</option>)}""",
     """            height: 36, borderRadius: 6, border: '1px solid rgb(234,236,240)',
            padding: '0 10px', fontSize: 14, fontWeight: 600,
            color: 'rgb(52,64,84)', backgroundColor: '#fff',
          }}
        >
          {VIEWS.map((v) => <option key={v}>{v}</option>)}"""),
    # Manage view: measured 14px/600 rgb(0,78,235), radius 8, 0.8px border + icon
    ("""              height: 36, padding: '0 14px', borderRadius: 8,
              border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
              fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)',
            }}
          >
            Manage view
          </button>""",
     """              height: 37, padding: '0 12px', borderRadius: 8,
              border: '0.8px solid rgb(234,236,240)', backgroundColor: '#fff',
              fontSize: 14, fontWeight: 600, color: 'rgb(0,78,235)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}
          >
            <IconSettings size={16} color="rgb(0,78,235)" />
            Manage view
          </button>"""),
    # + New: measured 14px/500 white
    ("""              height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              opacity: 0.5, cursor: 'not-allowed',
            }}
          >
            + New
          </button>""",
     """              height: 36, padding: '0 12px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              opacity: 0.5, cursor: 'not-allowed',
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <IconPlus size={16} color="#fff" />
            New
          </button>"""),
    # "Meetings" filter measured 14px/600 rgb(0,78,235) — was missing entirely
    ("""        <div className="ml-auto flex items-center gap-2">""",
     """        <select
          title="Event type filter (measured in GHL as 'Meetings')"
          style={{
            height: 36, borderRadius: 6, border: '1px solid rgb(234,236,240)',
            padding: '0 10px', fontSize: 14, fontWeight: 600,
            color: 'rgb(0,78,235)', backgroundColor: '#fff',
          }}
        >
          <option>Meetings</option>
        </select>

        <div className="ml-auto flex items-center gap-2">"""),
], imports="import { IconPlus, IconSettings } from '../components/Icon'\n")
