"""Saved after-inspection reports, every change a version (2026-11-12).

The owner's rule: anything in the report can be changed after it is made — by hand on the Owen
AI page (Reports tab) or by asking Owen AI in the chat — and nothing is lost: every save is a
new OwenAiReportVersion, and restoring an old one is just another save. Owen AI and Owen work on
the SAME saved content, so neither overwrites the other: every save names the version it
started from (`base_version`) and is refused (`Conflict`) when that is no longer the latest.

Files, under store_dir()/<report id>/ (the owen_ai_pictures volume — both containers write):

    lib/        the report's OWN copies of its photos ('021-3fa9c1d2.jpg'), so the report keeps
                working after job_photos' 14-day cleanup; every picture in the folders it was
                made from is copied too, so a later edit can use a photo the first draft did not
    pictures/   the option pictures, '<hash>.png' (infographics.py) and Owen's uploads
                ('upload-<sha>.jpg'); a picture is kept for as long as the report exists
    v<n>/       report.html (+ report.pdf) for version n

A version's spec stores photo paths RELATIVE to the report's folder; `load_spec` turns them back
into absolute paths for rendering and for Claude's Read tool.

Pictures, per option: `uploaded` (Owen's own file), `current` (made from exactly this text),
`stale` (the text changed since; the last picture is shown until Owen or Claude asks for a new
one), `missing` (none yet). A picture is made only on the FIRST save (when the switch is on) or
when the save names the option in `regenerate` — an edit never spends money by itself.

Facts (customer, address, date, the proposal's options and warranties) are read from Zuper
once, when the report is made, and kept on the report; `refresh_facts` reads them again.
Nothing here writes to Zuper or sends anything to anyone.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import OwenAiReport, OwenAiReportVersion
from . import config, infographics, pictures, report

SOURCES = ("ai", "page", "restore")
STATES = ("current", "stale", "uploaded", "missing")
LIB_MAX = 200                     # photos copied into one report's folder
UPLOAD_MAX_BYTES = 8 * 1024 * 1024
URL_PREFIX = "/api/owen-ai/inspection-reports/"
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(jpg|jpeg|png)$")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


class Conflict(Exception):
    """The report changed since the version this save started from: re-read and try again."""


class NotFound(LookupError):
    pass


def store_dir() -> Path:
    return config.report_store_dir()


def report_dir(report_id: int) -> Path:
    return store_dir() / str(int(report_id))


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


# ------------------------------------------------------------------------ photos & paths

def _walk_paths(value: Any, fn) -> Any:
    """Every photo's `path` in a spec (any dict holding a str `path`), through `fn`."""
    if isinstance(value, dict):
        return {k: (fn(v) if k == "path" and isinstance(v, str) else _walk_paths(v, fn))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_paths(v, fn) for v in value]
    return value


def _lib_name(src: Path) -> str:
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9]", "", src.stem)[:20] or "photo"
    return "%s-%s%s" % (stem, digest, src.suffix.lower())


def _import(src: Path, lib: Path) -> str:
    """Copy one photo into the report's lib/ (once per content); its path relative to the
    report's folder."""
    lib.mkdir(parents=True, exist_ok=True)
    name = _lib_name(src)
    if not (lib / name).is_file():
        shutil.copyfile(src, lib / name)
    return "lib/" + name


def _import_library(folders: set[Path], lib: Path) -> int:
    """Every picture in the folders the report used — so the page can offer them later."""
    n = len(list(lib.glob("*"))) if lib.is_dir() else 0
    added = 0
    for folder in sorted(folders):
        for f in sorted(folder.iterdir()) if folder.is_dir() else []:
            if n >= LIB_MAX:
                return added
            if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES:
                before = (lib / _lib_name(f)).is_file()
                _import(f, lib)
                if not before:
                    n += 1
                    added += 1
    return added


def _roots(report_id: int | None) -> list[Path]:
    roots = []
    if report_id is not None:
        roots.append(report_dir(report_id))
    roots.append(config.photo_dir())
    return roots


def _store_spec(s: report.Spec, rdir: Path, library: bool) -> dict:
    """The validated spec with every photo copied into the report's lib/ and its path made
    relative ('lib/...'). `library`: also copy the rest of each source folder."""
    lib = rdir / "lib"
    used: set[Path] = set()

    def rel(p: str) -> str:
        path = Path(p)
        if path.is_relative_to(rdir.resolve()):
            return path.relative_to(rdir.resolve()).as_posix()
        used.add(path.parent)
        return _import(path, lib)
    out = _walk_paths(s.model_dump(mode="json", exclude_none=True), rel)
    if library and used:
        _import_library(used, lib)
    return out


def load_spec(report_id: int, spec: dict) -> dict:
    """A stored spec with absolute photo paths (for rendering and for Claude's Read tool)."""
    rdir = report_dir(report_id).resolve()
    return _walk_paths(spec, lambda p: str((rdir / p).resolve()) if not Path(p).is_absolute()
                       else p)


def library(report_id: int) -> list[str]:
    lib = report_dir(report_id) / "lib"
    return sorted("lib/" + f.name for f in lib.iterdir()
                  if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES) if lib.is_dir() else []


# ------------------------------------------------------------------------------ pictures

def _pictures(db: Session, row: OwenAiReport, s: report.Spec, f: report.Facts,
              regenerate: set[str], first: bool, warnings: list[str]) -> tuple[dict, float]:
    """Each option's picture state for this save; makes a picture only when allowed."""
    if not s.gbb:
        return {}, 0.0
    folder = report_dir(row.id) / "pictures"
    prev = _latest(db, row.id)
    before = (prev.pictures or {}) if prev else {}
    out: dict[str, dict] = {}
    cost = 0.0
    for o in s.gbb.options:
        war = report.option_warranty(o, f)
        h = infographics.picture_hash(o, war)
        if o.picture_upload and (folder / o.picture_upload).is_file():
            out[o.name] = {"file": o.picture_upload, "hash": h, "state": "uploaded"}
            continue
        if o.picture_upload:
            warnings.append("%s: the uploaded picture %s is gone; using the AI picture."
                            % (o.name, o.picture_upload))
        if (folder / ("%s.png" % h)).is_file() and o.name not in regenerate:
            out[o.name] = {"file": "%s.png" % h, "hash": h, "state": "current"}
            continue
        if o.name in regenerate or (first and config.report_pictures_enabled()):
            try:
                png, usd = infographics.generate(db, o, war)
            except (infographics.Refused, pictures.ImageError) as exc:
                warnings.append("%s picture not made: %s" % (o.name, exc))
            else:
                cost += usd
                out[o.name] = {"file": infographics.save(folder, h, png), "hash": h,
                               "state": "current", "cost_usd": usd}
                continue
        old = before.get(o.name) or {}
        if old.get("file") and (folder / old["file"]).is_file():
            out[o.name] = {"file": old["file"], "hash": h, "state": "stale"}
            warnings.append("%s picture is out of date: its text changed since it was made. "
                            "Ask for a new one (about $%.2f) or upload one." % (
                                o.name, infographics.EST_COST))
        else:
            out[o.name] = {"file": None, "hash": h, "state": "missing"}
            warnings.append("%s has no picture yet%s." % (
                o.name, "" if config.report_pictures_enabled() else
                " (" + config.REPORT_PICTURES_OFF.rstrip(".") + ")"))
    return out, round(cost, 4)


def _picture_paths(report_id: int, pics: dict) -> dict[str, Path | None]:
    folder = report_dir(report_id) / "pictures"
    return {k: (folder / v["file"]) if v.get("file") else None for k, v in pics.items()}


# ------------------------------------------------------------------------------ versions

def _latest(db: Session, report_id: int) -> OwenAiReportVersion | None:
    return db.scalars(select(OwenAiReportVersion).where(
        OwenAiReportVersion.report_id == report_id).order_by(
        OwenAiReportVersion.number.desc())).first()


def get(db: Session, report_id: int) -> OwenAiReport:
    row = db.get(OwenAiReport, int(report_id))
    if row is None:
        raise NotFound("no saved report %s" % report_id)
    return row


def version(db: Session, report_id: int, number: int | None = None) -> OwenAiReportVersion:
    q = select(OwenAiReportVersion).where(OwenAiReportVersion.report_id == report_id)
    q = q.where(OwenAiReportVersion.number == number) if number else \
        q.order_by(OwenAiReportVersion.number.desc())
    v = db.scalars(q).first()
    if v is None:
        raise NotFound("report %s has no version %s" % (report_id, number or ""))
    return v


def _render_files(report_id: int, number: int, s: report.Spec, f: report.Facts,
                  pics: dict, warnings: list[str]) -> bool:
    d = report_dir(report_id) / ("v%d" % number)
    d.mkdir(parents=True, exist_ok=True)
    html = report.render(s, None, f, _picture_paths(report_id, pics), _roots(report_id))
    (d / "report.html").write_text(html, "utf-8")
    why = report.make_pdf(d / "report.html", d / "report.pdf")
    if why:
        warnings.append("PDF not made: " + why + ".")
    return why is None


def _save(db: Session, row: OwenAiReport, spec: Any, source: str, author: str | None,
          note: str | None, regenerate: set[str], first: bool) -> OwenAiReportVersion:
    rdir = report_dir(row.id)
    s = report.validate(spec, None, _roots(row.id))
    f = report.facts_from_dict(row.facts)
    warnings = list(f.warnings)
    stored = _store_spec(s, rdir, library=True)
    s = report.validate(load_spec(row.id, stored), None, _roots(row.id))
    pics, cost = _pictures(db, row, s, f, {x.upper() for x in regenerate}, first, warnings)
    number = row.current_version + 1
    has_pdf = _render_files(row.id, number, s, f, pics, warnings)
    v = OwenAiReportVersion(report_id=row.id, number=number, spec=stored, source=source,
                            author=author, note=(note or "")[:300] or None, pictures=pics,
                            warnings=warnings, has_pdf=has_pdf, cost_usd=cost or None)
    db.add(v)
    row.current_version = number
    row.updated_at = _now()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("Someone saved this report at the same moment; read it again and "
                       "redo the change.") from None
    db.refresh(v)
    return v


def create(db: Session, spec: Any, *, author: str | None = None, source: str = "ai",
           facts: report.Facts | None = None) -> tuple[OwenAiReport, OwenAiReportVersion]:
    """Validate, read the facts from Zuper (unless given), copy the photos, make the pictures
    (switch on), render: version 1. Raises report.SpecError, LookupError, ZuperError."""
    s = report.validate(spec, None, _roots(None))
    f = facts or report.fetch_facts(s.job_uid, s.proposal)
    row = OwenAiReport(job_uid=f.job_uid, job_number=f.job_number, customer=f.customer,
                       current_version=0, facts={**report.facts_to_dict(f),
                                                 "warnings": list(f.warnings)},
                       created_by=author)
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        v = _save(db, row, s, source, author, None, set(), first=True)
    except Exception:
        db.delete(row)
        db.commit()
        shutil.rmtree(report_dir(row.id), ignore_errors=True)
        raise
    return row, v


def update(db: Session, report_id: int, spec: Any, base_version: int, *, source: str,
           author: str | None = None, note: str | None = None,
           regenerate: list[str] | None = None, refresh_facts: bool = False
           ) -> OwenAiReportVersion:
    """A new version from a changed spec. Conflict when `base_version` is not the latest."""
    row = get(db, report_id)
    if int(base_version) != row.current_version:
        raise Conflict("This report is now at version %d, not %d: someone changed it. Read it "
                       "again and redo the change on the latest version."
                       % (row.current_version, int(base_version)))
    if refresh_facts:
        s = report.validate(spec, None, _roots(row.id))
        f = report.fetch_facts(row.job_uid, s.proposal)
        row.facts = {**report.facts_to_dict(f), "warnings": list(f.warnings)}
        row.customer = f.customer
    return _save(db, row, spec, source, author, note, set(regenerate or []), first=False)


def restore(db: Session, report_id: int, number: int, base_version: int, *,
            author: str | None = None) -> OwenAiReportVersion:
    old = version(db, report_id, number)
    return update(db, report_id, load_spec(report_id, old.spec), base_version,
                  source="restore", author=author, note="Restored version %d" % number)


def save_upload(report_id: int, data: bytes) -> str:
    """Owen's own picture for an option: checked to be a JPEG/PNG image, kept as
    pictures/upload-<sha>.<ext>. Returns the name the spec's `picture_upload` takes."""
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError
    if not data or len(data) > UPLOAD_MAX_BYTES:
        raise ValueError("The picture must be a JPEG or PNG up to 8 MB.")
    try:
        with Image.open(BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            im.verify()
    except (UnidentifiedImageError, OSError):
        raise ValueError("That file is not a picture.") from None
    if fmt not in ("JPEG", "PNG"):
        raise ValueError("The picture must be a JPEG or PNG.")
    name = "upload-%s.%s" % (hashlib.sha256(data).hexdigest()[:16],
                             "jpg" if fmt == "JPEG" else "png")
    folder = report_dir(report_id) / "pictures"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return name


def preview(db: Session, report_id: int, spec: Any) -> str:
    """The page as it would look with this spec — nothing saved, no picture made."""
    row = get(db, report_id)
    s = report.validate(spec, None, _roots(row.id))
    f = report.facts_from_dict(row.facts)
    # first=False and nothing to regenerate: _pictures can never call OpenAI here.
    pics, _ = _pictures(db, row, s, f, set(), False, [])
    return report.render(s, None, f, _picture_paths(row.id, pics), _roots(row.id))


# ------------------------------------------------------------------------------ files & out

def file_path(report_id: int, rel: str) -> Path | None:
    """A file of this report to serve: lib/<name>, pictures/<name> or v<n>/report.html|pdf.
    Anything else, or anything outside the report's folder, is None."""
    parts = rel.split("/")
    if len(parts) != 2:
        return None
    folder, name = parts
    ok = (folder in ("lib", "pictures") and FILE_NAME.match(name)) or \
        (re.fullmatch(r"v\d{1,4}", folder) and name in report.FILES)
    if not ok:
        return None
    root = report_dir(report_id).resolve()
    p = (root / folder / name).resolve()
    return p if p.is_relative_to(root) and p.is_file() else None


def urls(report_id: int, number: int, has_pdf: bool) -> dict:
    base = "%s%d/files/v%d/" % (URL_PREFIX, report_id, number)
    return {"html_url": base + "report.html", "pdf_url": (base + "report.pdf") if has_pdf
            else None}


def version_out(v: OwenAiReportVersion) -> dict:
    return {"number": v.number, "source": v.source, "author": v.author, "note": v.note,
            "created_at": _iso(v.created_at), "cost_usd": v.cost_usd, "has_pdf": v.has_pdf,
            "warnings": v.warnings or [], "pictures": v.pictures or {},
            **urls(v.report_id, v.number, v.has_pdf)}


def out(db: Session, row: OwenAiReport, with_spec: bool = False) -> dict:
    v = version(db, row.id)
    d = {"id": row.id, "job_uid": row.job_uid, "job_number": row.job_number,
         "customer": row.customer, "current_version": row.current_version,
         "created_by": row.created_by, "created_at": _iso(row.created_at),
         "updated_at": _iso(row.updated_at), "latest": version_out(v)}
    if with_spec:
        d["spec"] = v.spec
        d["library"] = library(row.id)
        d["versions"] = [version_out(x) for x in db.scalars(
            select(OwenAiReportVersion).where(OwenAiReportVersion.report_id == row.id)
            .order_by(OwenAiReportVersion.number.desc()))]
        d["files_url"] = "%s%d/files/" % (URL_PREFIX, row.id)
        d["options"] = [{"name": o.get("name"), "warranty": o.get("warranty")}
                        for o in (row.facts or {}).get("options") or []]
    return d


def list_out(db: Session, job_uid: str | None = None, limit: int = 200) -> list[dict]:
    q = select(OwenAiReport).order_by(OwenAiReport.updated_at.desc()).limit(limit)
    if job_uid:
        q = q.where(OwenAiReport.job_uid == job_uid)
    return [out(db, r) for r in db.scalars(q)]


def find(db: Session, ref: str) -> OwenAiReport:
    """A report by its id, or the newest one for a job number."""
    ref = str(ref or "").strip().lstrip("#")
    if ref.isdigit():
        row = db.get(OwenAiReport, int(ref))
        if row is not None:
            return row
    row = db.scalars(select(OwenAiReport).where(OwenAiReport.job_number == ref)
                     .order_by(OwenAiReport.updated_at.desc())).first()
    if row is None:
        raise NotFound("no saved report with id or job number %s" % ref)
    return row


def dumps(d: Any) -> str:
    return json.dumps(d, ensure_ascii=False, default=str)


# ------------------------------------------------------------------------------ the tools

AI_AUTHOR = "Owen AI"


def _session():
    from ..db import SessionLocal
    return SessionLocal()


def _links(row: OwenAiReport, v: OwenAiReportVersion) -> list[str]:
    u = urls(row.id, v.number, v.has_pdf)
    lines = ["Give Owen these links exactly as written (markdown):",
             "[Download report (.html)](%s)" % u["html_url"]]
    if u["pdf_url"]:
        lines.append("[Download report (.pdf)](%s)" % u["pdf_url"])
    lines.append("He can also open it on the Owen AI page, Reports tab, to change anything.")
    return lines


def _picture_lines(row: OwenAiReport, v: OwenAiReportVersion) -> list[str]:
    folder = report_dir(row.id) / "pictures"
    out = []
    for name, p in (v.pictures or {}).items():
        if p.get("file") and p.get("state") in ("current", "stale", "uploaded") \
                and (folder / p["file"]).is_file():
            # The small JPEG copy, never the 2.5 MB PNG: a Read that big ended the turn.
            small = infographics.preview(folder / p["file"])
            out.append("- %s picture (%s): %s" % (name, p["state"], small.resolve()))
    if out:
        out.insert(0, "READ each new picture with the Read tool and check every word against "
                      "the spec (titles, steps, callouts, layers, labels, warranty). If a word "
                      "is wrong, call update_inspection_report with regenerate_pictures for "
                      "that option and tell Owen:")
    return out


def _tool_errors(fn):
    from ..zuper import client as zc

    def run(args: dict) -> str:
        try:
            return fn(args)
        except report.SpecError as exc:
            return ("ERROR: the spec is not valid — fix these and call again:\n"
                    + "\n".join("- " + p for p in exc.problems))
        except Conflict as exc:
            return "ERROR: %s Call get_inspection_report, then redo the change." % exc
        except (LookupError, ValueError) as exc:
            return "ERROR: %s" % exc
        except zc.ZuperError as exc:
            return "ERROR from Zuper: %s" % zc.sentence(exc)
    return run


@_tool_errors
def make_inspection_report(args: dict) -> str:
    with _session() as db:
        row, v = create(db, args, author=AI_AUTHOR, source="ai")
        lines = ["Saved report %d, version 1, for job #%s%s — a DRAFT for Owen to review "
                 "(nothing was sent)." % (row.id, row.job_number,
                                          ", " + row.customer if row.customer else "")]
        lines += _links(row, v) + _picture_lines(row, v)
        if v.warnings:
            lines += ["Warnings (tell Owen):"] + ["- " + w for w in v.warnings]
        lines.append("To change it later: get_inspection_report, then update_inspection_report "
                     "(a new version; old ones can be restored).")
        return "\n".join(lines)


@_tool_errors
def get_inspection_report(args: dict) -> str:
    with _session() as db:
        row = find(db, str(args.get("report") or ""))
        v = version(db, row.id)
        return dumps({
            "report": row.id, "job_number": row.job_number, "customer": row.customer,
            "version": v.number, "base_version_for_update": row.current_version,
            "last_change": {"by": v.author, "source": v.source, "note": v.note,
                            "at": _iso(v.created_at)},
            "spec": load_spec(row.id, v.spec),
            "library": [str((report_dir(row.id) / x).resolve()) for x in library(row.id)],
            "pictures": v.pictures or {}, "warnings": v.warnings or [],
            "how_to_change": "Change ONLY what Owen asked in this spec (he may have edited it "
                             "on the page - keep his changes), then call "
                             "update_inspection_report with base_version = "
                             "base_version_for_update. Photo paths: any `library` file or a "
                             "new job_photos / companycam_photos path."})


@_tool_errors
def update_inspection_report(args: dict) -> str:
    with _session() as db:
        if not isinstance(args.get("spec"), dict):
            raise ValueError("`spec` (the whole changed spec) is required")
        row = find(db, str(args.get("report") or ""))
        v = update(db, row.id, args["spec"], int(args.get("base_version") or 0),
                   source="ai", author=AI_AUTHOR, note=str(args.get("note") or "")[:300],
                   regenerate=[str(x) for x in args.get("regenerate_pictures") or []],
                   refresh_facts=bool(args.get("refresh_facts")))
        row = get(db, row.id)
        lines = ["Saved report %d, version %d (nothing was sent)." % (row.id, v.number)]
        lines += _links(row, v) + _picture_lines(row, v)
        if v.cost_usd:
            lines.append("New pictures cost $%.2f." % v.cost_usd)
        if v.warnings:
            lines += ["Warnings (tell Owen):"] + ["- " + w for w in v.warnings]
        return "\n".join(lines)


MAKE_DESCRIPTION = (
    report.DESCRIPTION.replace(
        "Returns markdown download links", "Saves it as an EDITABLE report (version 1) and "
        "returns its id and markdown download links")
    + " gbb? = the Good / Better / Best pages {intro?, options[GOOD, BETTER, BEST in order]: {"
    "name, headline, picture {steps[4-6] {title, text}, callouts[2-3], section_title, "
    "layers[3-5] top to bottom, top_title?, top_label, roof?, notes?}, scope[] (the full "
    "written steps), days?, warranty? (only if the proposal has none), coverage[0-3] {photo, "
    "areas[{points [[x,y],...] 0-1 FRACTIONS, kind covered|excluded, label?}], caption?}}, "
    "compare[] {label, GOOD, BETTER, BEST}}; galleries? [{title, photos[{photo, caption}]}]. "
    "NO PRICES anywhere: they come after AHS says how much it covers. When the picture switch "
    "is on, one AI picture per option is made (about $0.18 each).")
GET_DESCRIPTION = (
    "READ a saved inspection report: `report` = its id, or a job number for that job's newest "
    "report. Returns the CURRENT spec (with Owen's own edits from the page), its version, the "
    "photo library (absolute paths you can Read and use), the pictures' states (current, "
    "stale, uploaded, missing) and warnings. Use it before every change.")
UPDATE_DESCRIPTION = (
    "SAVE a change to a saved inspection report as a NEW version (old versions stay and can be "
    "restored on the page). `report` = id; `base_version` = the version get_inspection_report "
    "returned (a newer save by Owen makes this fail: read again and redo); `spec` = the WHOLE "
    "spec with only the asked change; `note` = what changed, in a few words; "
    "`regenerate_pictures` = option names whose AI picture to make again (about $0.18 each - "
    "only when Owen asked or a picture's words are wrong); `refresh_facts` = read the "
    "customer, address, date and the proposal again from Zuper. Never sent to anyone.")

SPECS = (
    ("make_inspection_report", MAKE_DESCRIPTION, report.input_schema(), make_inspection_report),
    ("get_inspection_report", GET_DESCRIPTION,
     {"type": "object", "properties": {"report": {"type": "string"}}, "required": ["report"]},
     get_inspection_report),
    ("update_inspection_report", UPDATE_DESCRIPTION,
     {"type": "object", "properties": {
         "report": {"type": "string"}, "base_version": {"type": "integer"},
         "spec": {"type": "object"}, "note": {"type": "string"},
         "regenerate_pictures": {"type": "array",
                                 "items": {"type": "string", "enum": list(report.TIERS)}},
         "refresh_facts": {"type": "boolean"}},
      "required": ["report", "base_version", "spec", "note"]},
     update_inspection_report),
)
