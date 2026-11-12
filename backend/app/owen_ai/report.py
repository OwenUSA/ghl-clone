"""make_inspection_report — the customer inspection report, as a DRAFT for Owen (2026-11-07).

The design is the approved sample (owen-ai-analysis/inspection-report-samples/build_sample_v9.py
for job #710, build_sample_728_v2.py for #728), ported here so nothing depends on those scripts:
the CSS, the click-to-zoom viewer and the water-path diagram are copied verbatim into `assets/`,
with the logo (pre-shrunk to 260 px, base64-embedded at render time).

Who writes what:
  * Claude writes the SPEC (validated strictly by `Spec`): the words, which photos, the marks
    (boxes as 0-1 fractions of the picture), severities. Text is escaped; only <b> and <i>
    survive. Photo paths must be files under `config.photo_dir()` (what job_photos saved).
  * The tool fetches the FACTS itself, so Claude cannot get them wrong (`fetch_facts`): the
    customer's name and service address from the Zuper job (the street is cleaned of city /
    "EE. UU." junk), the job number, the inspection date (the "Inspection In Progress" move,
    else the schedule), the inspector (who moved that stage, else OWEN_AI_REPORT_INSPECTOR_
    DEFAULT), and the proposal's options: name, total, deposit, recommended, warranty, the
    included steps and the price block (Retail Value / Special AHS Member Price / AHS credit /
    customer pays) parsed from the line-item description. A figure that cannot be parsed is
    shown as "—", never guessed. Header phone / license / office come from settings; a missing
    one renders "[placeholder]" and a warning.

`render(spec, photos_dir, facts)` is pure (no network). `write(spec)` validates, fetches, renders
and saves `<report_dir>/<id>/report.html` (+ `report.pdf` when a Chromium is available: the same
HTML printed headless, @page/print CSS), `meta.json` beside them; folders older than 90 days are
removed on every write. The API serves the files to the owner only (api.py). The page itself is
clean — no draft banner, no "[placeholder]" (owner, 2026-11-09); a missing header value is left
out and named in the warnings Claude passes to Owen.
"""
from __future__ import annotations

import base64
import contextlib
import html
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..frontdoor import service as frontdoor
from ..zuper import client as zc
from ..zuper.mapping import parse_time
from . import config
from .dossier import TZ, Reader, money, person, proposal_number

ASSETS = Path(__file__).with_name("assets")
ID_RE = re.compile(r"[A-Za-z0-9]{1,20}-\d{8}-\d{6}")
FILES = {"report.html": "text/html; charset=utf-8", "report.pdf": "application/pdf"}
URL_PREFIX = "/api/owen-ai/reports/"
DASH = "—"
MINUS = "\u2212"  # the minus sign, as in the sample
PDF_TIMEOUT = 120

# ------------------------------------------------------------------------------ the spec

_TAG = re.compile(r"<(?=[/!?A-Za-z])[^>]*>?")
_ALLOWED = {"<b>": ("b", True), "</b>": ("b", False), "<i>": ("i", True), "</i>": ("i", False)}


def _rich(v: str) -> str:
    """Only <b>, </b>, <i>, </i>, balanced. Anything tag-like is refused (the text is escaped
    anyway; refusing tells Claude early)."""
    stack: list[str] = []
    for m in _TAG.finditer(v):
        tag = m.group(0).lower()
        if tag not in _ALLOWED:
            raise ValueError("only <b> and <i> are allowed, not %r" % m.group(0)[:20])
        name, opening = _ALLOWED[tag]
        if opening:
            stack.append(name)
        elif not stack or stack.pop() != name:
            raise ValueError("unbalanced %s" % tag)
    if stack:
        raise ValueError("<%s> is never closed" % stack[-1])
    return v


def _plain(v: str) -> str:
    if _TAG.search(v):
        raise ValueError("no HTML in this field (only the longer text fields allow <b>/<i>)")
    return v


def Rich(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n),
                     AfterValidator(_rich)]


def Plain(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n),
                     AfterValidator(_plain)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Severity = Literal["now", "soon", "watch"]


class Mark(_Strict):
    shape: Literal["ellipse", "rect", "line"]
    box: list[float] = Field(min_length=4, max_length=4, description=(
        "[x0, y0, x1, y1] as 0-1 fractions of the picture (0 = left/top, 1 = right/bottom). "
        "For a line: its two end points."))
    color: Literal["red", "orange", "blue", "green"] = "red"
    label: Plain(24) = ""

    @model_validator(mode="after")
    def _box(self) -> Mark:
        if any(not (0.0 <= v <= 1.0) for v in self.box):
            raise ValueError("box values must be fractions between 0 and 1")
        x0, y0, x1, y1 = self.box
        if self.shape != "line" and (x1 <= x0 or y1 <= y0):
            raise ValueError("an %s box needs x0 < x1 and y0 < y1" % self.shape)
        return self


class Photo(_Strict):
    path: str = Field(description="a file job_photos saved (its `path`)")
    num: int | None = Field(None, ge=1, le=9999, description=(
        "the photo number stamped on it; default = the number in the file name (007.jpg -> 7)"))
    marks: list[Mark] = Field(default_factory=list, max_length=6)
    brighten: bool = False
    crop_gps: bool = Field(True, description="hide the GPS/time strip at the bottom")

    @field_validator("path")
    @classmethod
    def _inside(cls, v: str, info: ValidationInfo) -> str:
        # `roots`: the folders a photo may come from — job_photos' folder, and for a SAVED
        # report (saved_reports.py) also that report's own folder. A relative path is tried
        # against each root in order.
        ctx = info.context or {}
        roots = [Path(r).resolve() for r in (ctx.get("roots")
                                             or [ctx.get("photos_dir") or config.photo_dir()])]
        p = Path(v)
        cands = [p.resolve()] if p.is_absolute() else [(r / p).resolve() for r in roots]
        for c in cands:
            if any(c.is_relative_to(r) for r in roots) and c.is_file() \
                    and c.suffix.lower() in (".jpg", ".jpeg", ".png"):
                return str(c)
        if not any(c.is_relative_to(r) for c in cands for r in roots):
            raise ValueError("photo paths must be files job_photos saved (under %s)" % roots[0])
        raise ValueError("no such picture: %s" % v)

    @model_validator(mode="after")
    def _num(self) -> Photo:
        if self.num is None:
            # The number in the file name: '007.jpg' -> 7, a saved report's copy
            # '007-3fa9c1d2.jpg' -> 7, a CompanyCam photo '104a.jpg' -> 104.
            m = re.match(r"(\d{1,4})(?!\d)", Path(self.path).stem)
            if not m or not 1 <= int(m.group(1)) <= 9999:
                raise ValueError("`num` is required when the file name is not a number")
            self.num = int(m.group(1))
        return self


class Badge(_Strict):
    # "hide" = no badge on the page at all (owner, 2026-11-09: "just remove that part").
    severity: Literal["now", "soon", "watch", "none", "hide"]
    text: Rich(300) = ""


class Hero(_Strict):
    cause_photo: Photo
    inside_photo: Photo | None = None
    captions: list[Plain(200)] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _captions(self) -> Hero:
        if len(self.captions) != (2 if self.inside_photo else 1):
            raise ValueError("one caption per hero photo")
        return self


class Location(_Strict):
    photo: Photo
    where_text: Rich(800)
    caption: Plain(200) = ""
    entry_label: Plain(40) = Field("", description="the diagram's step 1, e.g. 'Water gets in "
                                   "at the open joint'")


class Finding(_Strict):
    severity: Severity
    title: Plain(120)
    photos: list[Photo] = Field(min_length=1, max_length=2)
    refs: Plain(80) = Field("", description="every photo number that shows it, e.g. '6, 12, 20'")
    saw: Rich(1200)
    means: Rich(1200)
    why: Rich(800) = ""
    like: Rich(600) = ""
    fix: Rich(1200)
    prevent: Rich(600) = ""


class InsideItem(_Strict):
    state: Literal["yes", "no", "not_checked"]
    text: Rich(300)


class Inside(_Strict):
    title: Plain(80) = "Damage inside your home"
    photo: Photo | None = None
    caption: Plain(200) = ""
    items: list[InsideItem] = Field(min_length=1, max_length=12)
    after_finding: int | None = Field(None, ge=1, le=8, description=(
        "show this block right after finding N (default: after the last finding)"))


class Word(_Strict):
    term: Plain(30)
    text: Plain(200)


class OptionText(_Strict):
    fixes: Rich(300) = ""
    includes: Rich(500) = ""


# --- the after-inspection report's Good / Better / Best part (2026-11-12, job #292's approved
# draft). NO PRICES anywhere in it: they are shown only after AHS says how much it covers.

TIERS = ("GOOD", "BETTER", "BEST")
MONEY = re.compile(r"\$\s?\d|\b\d[\d,]*(?:\.\d\d)?\s?(?:dollars|usd)\b", re.IGNORECASE)
NO_MONEY = ("no prices in the after-inspection report: prices are shown only after AHS says how "
            "much it covers")


def _no_money(v: str) -> str:
    if MONEY.search(v or ""):
        raise ValueError(NO_MONEY)
    return v


def Words(n: int):
    """Plain text with no price in it."""
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n),
                     AfterValidator(_plain), AfterValidator(_no_money)]


def RichWords(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n),
                     AfterValidator(_rich), AfterValidator(_no_money)]


class Area(_Strict):
    points: list[list[float]] = Field(min_length=3, max_length=16, description=(
        "the area's corners [[x, y], ...] as 0-1 fractions of the picture, in order around it"))
    kind: Literal["covered", "excluded"] = Field("covered", description=(
        "covered = this option does the work here (filled in the option's colour); excluded = "
        "a problem this option leaves alone (dashed grey)"))
    label: Words(40) = ""

    @model_validator(mode="after")
    def _points(self) -> Area:
        for pt in self.points:
            if len(pt) != 2 or any(not (0.0 <= v <= 1.0) for v in pt):
                raise ValueError("each point is [x, y] with fractions between 0 and 1")
        return self


class Coverage(_Strict):
    photo: Photo
    areas: list[Area] = Field(min_length=1, max_length=6)
    caption: Words(120) = ""


class PictureStep(_Strict):
    title: Words(40)
    text: Words(90)


class PictureSpec(_Strict):
    """What the option's AI picture shows (infographics.py). Words only — no customer name,
    address or price ever goes into it."""
    steps: list[PictureStep] = Field(min_length=4, max_length=6)
    callouts: list[Words(45)] = Field(min_length=2, max_length=3, description=(
        "labels on the finished-roof overview, e.g. 'Tiles Reset & Secured'"))
    section_title: Words(40) = Field(description="e.g. 'Leak Repair', 'Peel & Stick'")
    layers: list[Words(40)] = Field(min_length=3, max_length=5, description=(
        "the cut-away's layers, top to bottom, e.g. 'Barrel Tile', 'Roof Deck (Plywood)'"))
    top_title: Words(40) = "Repair Area"
    top_label: Words(50) = Field(description="the top view's label, e.g. 'Leak Area Above the "
                                 "Bathroom'")
    roof: Words(240) = Field("", description="the roof to draw, e.g. 'terracotta barrel tile "
                             "main roof and a flat terrace roof beside the pool'")
    notes: Words(240) = Field("", description="what NOT to draw, e.g. 'no vent pipe'")


class OptionBlock(_Strict):
    name: Literal["GOOD", "BETTER", "BEST"]
    headline: Words(70) = Field(description="e.g. 'Targeted Leak Repair Above the Bathroom'")
    picture: PictureSpec
    picture_upload: Annotated[str, StringConstraints(
        pattern=r"^upload-[0-9a-f]{16}\.(jpg|png)$")] | None = Field(None, description=(
            "set by Owen's page when he uploads his own picture; leave it as it is"))
    scope: list[RichWords(400)] = Field(min_length=1, max_length=16, description=(
        "the full written scope, one step per item; may start with '<b>Title:</b> '"))
    days: Words(30) = Field("", description="time on site, e.g. '1 day', '2 days'")
    warranty: Words(40) = Field("", description="only when the proposal has none for this "
                                "option; default = the proposal's warranty")
    coverage: list[Coverage] = Field(default_factory=list, max_length=3)


class GalleryPhoto(_Strict):
    photo: Photo
    caption: Words(120) = ""


class Gallery(_Strict):
    title: Words(80)
    photos: list[GalleryPhoto] = Field(min_length=1, max_length=12)


class CompareRow(_Strict):
    label: Words(40)
    GOOD: Words(60) = ""
    BETTER: Words(60) = ""
    BEST: Words(60) = ""


class Gbb(_Strict):
    intro: RichWords(600) = ""
    show_prices: bool = Field(False, description=(
        "show each option's prices (retail, AHS member price, AHS credit, you pay, payment) - "
        "Owen's call (2026-11-12: yes once he wants them). They are READ from the proposal in "
        "Zuper (`proposal`), never typed: a '$' in any text is still refused."))
    options: list[OptionBlock] = Field(min_length=1, max_length=3)
    compare: list[CompareRow] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _order(self) -> Gbb:
        names = [o.name for o in self.options]
        if len(set(names)) != len(names) or names != sorted(names, key=TIERS.index):
            raise ValueError("options are GOOD, BETTER, BEST - each once, in that order")
        return self


class Spec(_Strict):
    job_uid: Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F-]{20,64}$")]
    proposal: Annotated[str, StringConstraints(pattern=r"^[A-Za-z-]{0,10}\d{1,8}$")] | None = \
        Field(None, description="e.g. DTR-129; default = the job's newest proposal")
    final: bool = Field(False, description="kept for compatibility; the page never shows a "
                        "draft banner")
    badge: Badge
    what_happened: Rich(1500)
    hero: Hero
    location: Location | None = None
    findings: list[Finding] = Field(min_length=1, max_length=8)
    inside: Inside | None = None
    not_done: Plain(120) = Field("", description="e.g. 'attic, water test'")
    weather: Plain(60)
    inspected_how: Plain(200) = Field(description="e.g. 'inside the garage · walked the "
                                      "garage roof · 24 photos'")
    roof: Plain(120) = ""
    until_fixed: list[Plain(200)] | None = Field(None, max_length=10)
    words: list[Word] | None = Field(None, max_length=8)
    option_text: dict[str, OptionText] | None = Field(None, description=(
        "plain-language 'What it fixes' / 'Includes' per option name (GOOD, BETTER, BEST); "
        "default = the proposal's own option description and steps"))
    options_note: Rich(600) = ""
    next_steps: Rich(400) = Field("", description="one extra sentence for page 1's call to "
                                  "action (e.g. unpriced advice: 'ask us for a price')")
    galleries: list[Gallery] | None = Field(None, max_length=4, description=(
        "photo pages after the findings, e.g. 'Inside the house', 'Cracked tiles marked by our "
        "technician'"))
    inspector: Plain(80) = Field("", description=(
        "the technician's name to show as Inspector, ONLY when Owen asks for another name than "
        "the one read from Zuper"))
    gbb: Gbb | None = Field(None, description=(
        "the Good / Better / Best pages: per option its AI picture, the area it covers on real "
        "photos and the written scope; then a comparison. NO PRICES."))


def _errors(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc") or ()) or "spec"
        msg = str(e.get("msg") or "invalid").removeprefix("Value error, ")
        out.append("%s: %s" % (loc, msg))
    return out


class SpecError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def validate(spec: Any, photos_dir: Path | str | None = None,
             roots: list[Path | str] | None = None) -> Spec:
    """`roots` (saved reports): every folder a photo may come from; default = photos_dir."""
    if isinstance(spec, Spec):
        return spec
    if not isinstance(spec, dict):
        raise SpecError(["spec: must be a JSON object"])
    try:
        return Spec.model_validate(spec, context={
            "photos_dir": str(photos_dir or config.photo_dir()),
            **({"roots": [str(r) for r in roots]} if roots else {})})
    except ValidationError as exc:
        raise SpecError(_errors(exc)) from None


@cache
def input_schema() -> dict:
    """The tool's input schema, from the model itself (so it cannot drift), titles dropped."""
    def strip(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k != "title" or not isinstance(v, str)}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(Spec.model_json_schema())


# ------------------------------------------------------------------------------ the facts

@dataclass
class Option:
    name: str
    total: float | None = None
    deposit: float | None = None
    recommended: bool = False
    accepted: bool = False
    description: str | None = None          # the option's own one-line description
    retail: float | None = None
    member: float | None = None
    credit: float | None = None             # negative
    customer_pays: float | None = None
    warranty: str | None = None
    steps: list[str] = field(default_factory=list)


@dataclass
class Facts:
    job_uid: str
    job_number: str
    customer: str | None = None
    last_name: str | None = None
    address: str | None = None
    date: datetime | None = None
    inspector: str | None = None
    phone: str | None = None
    license: str | None = None
    city: str | None = None
    proposal: str | None = None
    options: list[Option] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
COUNTRY_JUNK = {"ee. uu.", "ee.uu.", "ee uu", "eeuu", "usa", "u.s.a.", "us", "u.s.",
                "united states", "united states of america", "estados unidos"}


def _cap(s: Any) -> str:
    """'BRETT ' -> 'Brett'; mixed case ('McDonald') is kept."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s.title() if s and (s.isupper() or s.islower()) else s


def state_abbr(state: Any) -> str:
    s = re.sub(r"\s+", " ", str(state or "")).strip()
    return STATES.get(s.lower(), s.upper() if len(s) == 2 else s)


def clean_address(addr: dict) -> str | None:
    """Zuper's street can carry the city, the state, the zip and 'EE. UU.' (Google's Spanish
    'USA'); keep only the street part, then add city, state and zip from their own fields."""
    city = _cap(addr.get("city"))
    st = state_abbr(addr.get("state"))
    zp = re.sub(r"\s+", "", str(addr.get("zip_code") or addr.get("zipcode") or ""))
    drop = COUNTRY_JUNK | {x.lower() for x in (city, st, str(addr.get("state") or "").strip(),
                                                 str(addr.get("country") or "").strip()) if x}
    parts = []
    for part in str(addr.get("street") or "").split(","):
        p = re.sub(r"\s+", " ", part).strip()
        low = p.lower()
        if not p or low in drop:
            continue
        if re.fullmatch(r"(?:[a-z .]+\s)?\d{5}(?:-\d{4})?", low) and (
                not parts or not re.match(r"\d", low)):
            continue                       # "FL 33467", "Florida 33467", "33467"
        parts.append(p)
    street = ", ".join(parts)
    if street.isupper():
        street = street.title()
    tail = " ".join(x for x in (st, zp) if x)
    out = ", ".join(x for x in (street, city, tail) if x)
    return out or None


# --- the proposal's description (ported from proposal-photo-audit/pricing_parse.py)

NUM = r"\$\s?([\d,]+(?:\.\d+)?)"
LABELS = [
    ("retail", r"retail"),
    ("member", r"member price"),
    ("credit", r"ahs authori[sz]ed|ahs credit|authorized credit"),
    ("customer_pays", r"customer pays|your price|total proposed cost"),
]


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def clean_desc(text: str | None) -> str:
    if not text:
        return ""
    t = text.replace("\ufffd", "-").replace("\u2013", "-").replace("—", "-")
    t = t.replace("\\-", "-").replace("\\$", "$").replace("\\", "")
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"(?<![\w$])_|_(?![\w])", "", t)
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.MULTILINE)
    return t.replace("\xa0", " ")


def html_to_text(value: str) -> str:
    t = re.sub(r"(?i)<li[^>]*>", "\n* ", value)
    t = re.sub(r"(?i)</(p|li|div|h\d)>|<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t)


def price_block(t: str) -> dict:
    """{'retail', 'member', 'credit' (negative), 'customer_pays'} from the
    'Retail Value: $X | Special AHS Member Price: $Y | ...' line; missing keys are unknown."""
    raw = None
    for line in t.splitlines():
        if re.search(r"retail value|your price|total investment|standard rate", line, re.IGNORECASE) \
                and "$" in line:
            raw = line.strip(" *()\t")
            break
    out: dict[str, float] = {}
    if raw is None:
        return out
    for seg in raw.split("|"):
        m = re.search(r"([A-Za-z][^:$]*?)\s*:\s*(-?)\s*" + NUM, seg) or \
            re.search(r"([A-Za-z][^$]*?)\s+(-?)" + NUM, seg)
        if not m:
            continue
        label, val = m.group(1).strip(" (*").lower(), _num(m.group(3))
        if val is None:
            continue
        for key, pat in LABELS:
            if re.search(pat, label) and not (key == "credit" and "dream" in label):
                out.setdefault(key, -abs(val) if key == "credit" else val)
                break
    return out


WORDNUM = {"one": 1, "two": 2, "three": 3, "six": 6, "twelve": 12, "thirty": 30, "ninety": 90}


def warranty(t: str) -> str | None:
    """'Workmanship Warranty: 3 Months ...' -> '3 months'; '1 Year' -> '1 year'."""
    m = re.search(r"Warranty\s*[:\-]?\s*(\d+|one|two|three|six|twelve|thirty|ninety)\s*[- ]?\s*"
                  r"(days?|months?|monts?|years?)\b", t, re.IGNORECASE) or \
        re.search(r"(\d+)[- ](day|month|year)s?\s+workmanship warranty", t, re.IGNORECASE)
    if not m:
        return None
    n = m.group(1).lower()
    n = int(n) if n.isdigit() else WORDNUM[n]
    unit = re.sub(r"s$", "", m.group(2).lower()).replace("mont", "month").replace("monthh",
                                                                               "month")
    return "%d %s%s" % (n, unit, "" if n == 1 else "s")


def steps(t: str) -> list[str]:
    """The scope's top-level bullets, as short titles ('Tile Removal & Substrate Inspection')."""
    out: list[str] = []
    for line in t.splitlines():
        m = re.match(r"^(\s*)(\*|-|•|\d+\.)\s+(.*)$", line)
        if not m:
            continue
        s = re.sub(r"^((\*|-|•)\s+)+", "", m.group(3)).strip()
        if not s or len(m.group(1).expandtabs(4)) >= 2:
            continue
        if re.search(r"retail value|member price|^50% |upfront deposit|upon completion \(|"
                     r"^payment|^\(", s, re.IGNORECASE) or re.match(r"warranty", s, re.IGNORECASE):
            continue
        t2 = re.match(r"^([^:]{3,110}):\s", s)
        out.append(t2.group(1).strip() if t2 else " ".join(s.split()[:8]))
    return out


def option_of(o: dict) -> Option:
    texts = []
    for li in o.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        d = li.get("markdown_description")
        if not d and li.get("description"):
            d = html_to_text(str(li["description"]))
        texts.append(str(d or li.get("plain_text_description") or ""))
    t = clean_desc("\n\n".join(texts))
    pb = price_block(t)
    return Option(name=str(o.get("option_name") or o.get("name") or "Option").strip(),
                  total=money(o.get("total")), deposit=money(o.get("deposit")),
                  recommended=bool(o.get("is_recommended")), accepted=bool(o.get("is_accepted")),
                  description=re.sub(r"\s+", " ", str(o.get("option_description") or "")).strip()
                  or None,
                  retail=pb.get("retail"), member=pb.get("member"), credit=pb.get("credit"),
                  customer_pays=pb.get("customer_pays"), warranty=warranty(t), steps=steps(t))


def options_of(rec: dict, number: str, warnings: list[str],
               ahs: dict | None = None) -> list[Option]:
    """`ahs` = what Frontdoor says AHS authorized (`ahs_authorization`): an option whose
    description has no AHS credit takes `authorized_to_contractor` from it; one whose description
    has a different credit keeps its own and gets a warning — never overwritten."""
    out = []
    credit = (ahs or {}).get("authorized_to_contractor")
    filled: list[str] = []
    for o in rec.get("proposal_options") or []:
        if not isinstance(o, dict) or o.get("is_hidden"):
            continue
        opt = option_of(o)
        if credit is not None:
            if opt.credit is None:
                opt.credit = -abs(credit)
                filled.append(opt.name)
            elif abs(abs(opt.credit) - credit) >= 1:
                warnings.append("%s %s: the description's AHS credit is %s but Frontdoor says AHS "
                                "authorized %s on dispatch %s; the table shows the description's."
                                % (number, opt.name, fmt_money(abs(opt.credit)),
                                   fmt_money(credit), ahs.get("dispatch_id")))
        if opt.total is None:
            warnings.append("%s %s has no total in Zuper; its price is shown as %s."
                            % (number, opt.name, DASH))
        if opt.customer_pays is not None and opt.total is not None \
                and abs(opt.customer_pays - opt.total) >= 1:
            warnings.append("%s %s: the description says the customer pays %s but the option "
                            "total is %s; the table shows the total." % (
                                number, opt.name, fmt_money(opt.customer_pays),
                                fmt_money(opt.total)))
        missing = [k for k, v in (("retail value", opt.retail), ("member price", opt.member),
                                  ("AHS credit", opt.credit), ("warranty", opt.warranty))
                   if v is None]
        if missing:
            warnings.append("%s %s: could not read the %s from the description (shown as %s)."
                            % (number, opt.name, ", ".join(missing), DASH))
        out.append(opt)
    if filled:
        warnings.append("AHS credit for %s taken from Frontdoor: AHS authorized %s on dispatch %s"
                        "%s; on AHS's own estimate of %s the customer owes %s." % (
                            ", ".join(filled), fmt_money(credit), ahs.get("dispatch_id"),
                            (" on " + ahs["authorized_at"][:10]) if ahs.get("authorized_at")
                            else "", fmt_money(ahs.get("total")),
                            fmt_money(ahs.get("customer_owes"))))
    return out


AHS_JOB_ID_LABEL = "AHS Job ID"
FRONTDOOR_ACTION = "ahs_read"        # registry action on the owen_ai entry


def frontdoor_refusal() -> str | None:
    """May Owen AI read Frontdoor now? The server switch and login (app/frontdoor), then the
    registry's owen_ai "ahs_read" level. Used by the report and by tools.frontdoor_read."""
    why = frontdoor.refusal()
    if why:
        return why
    from ..db import SessionLocal
    from ..registry import service as registry
    with SessionLocal() as db:
        return registry.level_refusal(db, config.KEY, FRONTDOOR_ACTION)


def ahs_authorization(rec: dict, warnings: list[str]) -> dict | None:
    """What AHS authorized on this job, read from Frontdoor by the job's "AHS Job ID" field.
    None (and no request) when the job has no AHS Job ID or Frontdoor reading is off; a failed
    read is a warning, never a failed report."""
    ahs_id = next((str(f.get("value") or "").strip() for f in rec.get("custom_fields") or []
                   if isinstance(f, dict) and str(f.get("label") or "").strip().lower()
                   == AHS_JOB_ID_LABEL.lower() and str(f.get("value") or "").strip()), None)
    if not ahs_id or frontdoor_refusal():
        return None
    try:
        a = frontdoor.authorization_for(ahs_id)
    except Exception as exc:  # noqa: BLE001 — Frontdoor must never break the report
        warnings.append("Could not read AHS's authorization from Frontdoor (dispatch %s): %s"
                        % (ahs_id, frontdoor.sentence(exc)))
        return None
    if a is None:
        warnings.append("Frontdoor shows no authorization on AHS dispatch %s yet." % ahs_id)
    return a


def _inspection_move(rec: dict) -> dict | None:
    for rx in (r"inspection in progress", r"inspect"):
        for s in sorted((x for x in rec.get("job_status") or [] if isinstance(x, dict)),
                        key=lambda x: str(x.get("created_at") or "")):
            if re.search(rx, str(s.get("status_name") or ""), re.IGNORECASE) and \
                    parse_time(s.get("created_at")):
                return s
    return None


def fetch_facts(job_uid: str, proposal: str | None = None) -> Facts:
    """Read-only: GET /jobs/{uid}, GET /estimate?filter.job=, GET /estimate/{uid}."""
    r = Reader()
    rec = zc.data_of(r.get("/jobs/" + job_uid))
    if not isinstance(rec, dict) or str(rec.get("job_uid") or "").lower() != job_uid.lower():
        raise LookupError("Zuper has no job %s" % job_uid)
    warnings: list[str] = []
    number = str(rec.get("work_order_number") or "").strip()
    if not number:
        raise LookupError("job %s has no job number in Zuper" % job_uid)
    cust = rec.get("customer") if isinstance(rec.get("customer"), dict) else {}
    addr = next((a for a in (rec.get("customer_address"),
                             (rec.get("property") or {}).get("property_address")
                             if isinstance(rec.get("property"), dict) else None,
                             rec.get("customer_billing_address"))
                 if isinstance(a, dict) and a.get("street")), {})
    first = _cap(cust.get("customer_first_name") or addr.get("first_name"))
    last = _cap(cust.get("customer_last_name") or addr.get("last_name"))
    f = Facts(job_uid=str(rec["job_uid"]), job_number=number,
              customer=" ".join(x for x in (first, last) if x) or None,
              last_name=last or None, address=clean_address(addr) if addr else None,
              warnings=warnings)
    move = _inspection_move(rec)
    when = parse_time(move.get("created_at")) if move else None
    when = when or parse_time(rec.get("scheduled_start_time"))
    f.date = when.astimezone(TZ) if when and when.tzinfo else (
        when.replace(tzinfo=UTC).astimezone(TZ) if when else None)
    f.inspector = (person(move.get("done_by")) if move else None) or \
        config.report_setting("INSPECTOR_DEFAULT")
    f.phone = config.report_setting("PHONE")
    f.license = config.report_setting("LICENSE")
    f.city = config.report_setting("CITY")
    for label, value in (("customer name", f.customer), ("service address", f.address),
                         ("inspection date", f.date)):
        if not value:
            warnings.append("Zuper has no %s for this job: the report shows %s."
                            % (label, config.PLACEHOLDER))
    for label, env, value in (("phone", "OWEN_AI_REPORT_PHONE", f.phone),
                              ("license", "OWEN_AI_REPORT_LICENSE", f.license),
                              ("office city", "OWEN_AI_REPORT_CITY", f.city),
                              ("inspector", "OWEN_AI_REPORT_INSPECTOR_DEFAULT", f.inspector)):
        if not value:
            warnings.append("No %s (%s is not set): the report shows %s."
                            % (label, env, config.PLACEHOLDER))

    try:
        rows = [x for x in zc.rows_of(r.get("/estimate", {"filter.job": f.job_uid, "count": 50,
                                                          "page": 1}))
                if isinstance(x, dict) and not x.get("is_deleted")]
    except zc.ZuperError as exc:
        rows = []
        warnings.append("Could not read the proposals (%s): no options table." % zc.sentence(exc))
    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    pick = None
    if proposal:
        want = proposal.strip().upper()
        pick = next((x for x in rows if (proposal_number(x) or "").upper() == want
                     or str(x.get("estimate_no") or "") == want.lstrip("DTR-")), None)
        if pick is None:
            warnings.append("This job has no proposal %s: no options table." % proposal)
    elif rows:
        pick = rows[0]
        if len(rows) > 1:
            warnings.append("The job has %d proposals; the table uses the newest, %s. Pass "
                            "`proposal` to use another." % (len(rows), proposal_number(pick)))
    elif not warnings or "proposals" not in warnings[-1]:
        warnings.append("The job has no proposal yet: no options table.")
    if pick is not None:
        f.proposal = proposal_number(pick) or "the proposal"
        try:
            full = zc.data_of(r.get("/estimate/" + str(pick["estimate_uid"])))
            f.options = options_of(full if isinstance(full, dict) else {}, f.proposal, warnings,
                                   ahs_authorization(rec, warnings))
        except zc.ZuperError as exc:
            warnings.append("Could not read %s (%s): no options table."
                            % (f.proposal, zc.sentence(exc)))
        if not f.options and not any(f.proposal in w for w in warnings):
            warnings.append("%s has no options: no options table." % f.proposal)
    return f


# ------------------------------------------------------------------------------ rendering

RGB = {"red": (220, 38, 38), "orange": (234, 120, 0), "blue": (37, 99, 235),
       "green": (22, 163, 74)}
SEV = {"now": ("Fix now", "red"), "soon": ("Fix soon", "orange"), "watch": ("Watch", "blue"),
       "none": ("No repair needed", "green")}
CHECK = {"yes": ("yes", "Yes"), "no": ("no", "No"), "not_checked": ("na", "Not checked")}
EXTRA_CSS = (".badge.orange{background:var(--orange)}.badge.blue{background:var(--blue)}"
             ".badge.green{background:var(--green)}")
DEFAULT_UNTIL = [
    "Keep things you care about out from under the stains.",
    "If it drips, put a bucket under it.",
    "If the ceiling starts to sag, stay out from under it and call us.",
    "If water reaches a light or outlet, turn that circuit off and call an electrician.",
    "Mark the edge of each stain in pencil. Call us if it grows.",
    "Please don't climb on the roof.",
]
DEFAULT_WORDS = [
    ("Flashing", "metal that seals where the roof meets a wall or a pipe."),
    ("Underlayment", "the waterproof sheet under the tiles."),
    ("Valley", "the channel where two slopes meet."),
    ("Hip / ridge", ("the corners and top line of the roof, covered with round cap tiles set "
                     "in mortar.")),
]
WATER_DEFAULT_LABEL = "Water gets in at the worn corner"   # the text in assets/water.svg


@cache
def _asset(name: str) -> str:
    return (ASSETS / name).read_text("utf-8")


@cache
def logo_uri() -> str:
    return "data:image/png;base64," + base64.b64encode(
        (ASSETS / "dtr-logo.png").read_bytes()).decode()


def esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def rich(s: str) -> str:
    """Escaped, then the four allowed tags back (validation already checked they balance)."""
    return re.sub(r"&lt;(/?)([bi])&gt;", r"<\1\2>", html.escape(s or "", quote=True),
                  flags=re.IGNORECASE).replace("<B>", "<b>").replace("</B>", "</b>") \
        .replace("<I>", "<i>").replace("</I>", "</i>")


def fmt_money(v: float | None, neg: bool = False) -> str:
    if v is None:
        return DASH
    a = abs(v)
    s = f"${a:,.0f}" if a == int(a) else f"${a:,.2f}"
    return (MINUS + s) if (neg or v < 0) else s


def _font(size: int):
    from PIL import ImageFont
    for f in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        with contextlib.suppress(OSError):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def annotate(p: Photo) -> str:
    """The sample's photo(): an annotated COPY — GPS strip cropped, marks with labels kept
    inside the frame, a 'Photo N' stamp; sizes scale with the picture (1024 px = the sample)."""
    from PIL import Image, ImageDraw, ImageEnhance
    with Image.open(p.path) as src:
        im = src.convert("RGB")
    w, h = im.size
    k = max(w, h) / 1024

    def s(n: float) -> int:
        return max(1, round(n * k))
    crop = s(70) if p.crop_gps else 0
    im = im.crop((0, 0, w, h - crop))
    if p.brighten:
        im = ImageEnhance.Contrast(ImageEnhance.Brightness(im).enhance(1.25)).enhance(1.6)
    d = ImageDraw.Draw(im)
    fsize = s(34)
    f = _font(fsize)
    for m in p.marks:
        box = (round(m.box[0] * w), round(m.box[1] * h), round(m.box[2] * w),
               round(m.box[3] * h))
        color = RGB[m.color]
        if m.shape == "ellipse":
            d.ellipse(box, outline=color, width=s(8))
        elif m.shape == "rect":
            d.rectangle(box, outline=color, width=s(8))
        else:
            d.line(box, fill=color, width=s(10))
        if m.label:
            tw = d.textlength(" %s " % m.label, font=f)
            th = fsize + s(14)
            x = min(max(s(4), box[0]), im.width - tw - s(10))
            y = box[1] - th - s(6) if box[1] - th - s(6) >= s(4) else \
                min(box[3] + s(6), im.height - th - s(4))
            d.rounded_rectangle((x, y, x + tw + s(6), y + th), radius=s(9), fill=color)
            d.text((x + s(3), y + s(5)), " %s " % m.label, fill="white", font=f)
    tag = "Photo %s" % p.num
    tw = d.textlength(tag, font=f)
    d.rounded_rectangle((s(12), im.height - fsize - s(30), s(34) + tw, im.height - s(12)),
                        radius=s(8), fill=(17, 24, 39))
    d.text((s(23), im.height - fsize - s(25)), tag, fill="white", font=f)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=84)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _img(p: Photo, alt: str, cache_: dict) -> str:
    key = p.model_dump_json()
    if key not in cache_:
        cache_[key] = annotate(p)
    return '<img class="zoom" src="%s" alt="%s">' % (cache_[key], esc(alt))


def card(n: int, fd: Finding, imgs: dict) -> str:
    label, cls = SEV[fd.severity]
    pics = "".join(_img(p, fd.title, imgs) for p in fd.photos)
    refs = fd.refs or ", ".join(str(p.num) for p in fd.photos)
    word = "Photos" if re.search(r"[,\u2013\-·&]| and ", refs) else "Photo"
    rows = "".join("<dt>%s</dt><dd>%s</dd>" % (head, rich(getattr(fd, key))) for head, key in (
        ("What we saw", "saw"), ("What it means", "means"), ("Why it happens", "why"),
        ("Think of it like this", "like"), ("How we fix it", "fix"),
        ("How to prevent it", "prevent")) if getattr(fd, key))
    return f"""<section class="card">
 <header><span class="num">{n}</span><h3>{esc(fd.title)}</h3><span class="pill {cls}">{label}</span></header>
 <div class="cg"><div class="ph n{len(fd.photos)}">{pics}<p class="refs">{word} {esc(refs)} · click a photo to zoom</p></div><dl>{rows}</dl></div>
</section>"""


def _date_long(d: datetime | None) -> str:
    return "%s %d, %d" % (d.strftime("%a, %b"), d.day, d.year) if d else config.PLACEHOLDER


def _date_short(d: datetime | None) -> str:
    return "%s %d, %d" % (d.strftime("%b"), d.day, d.year) if d else None


def _ph(v: str | None) -> str:
    return esc(v) if v else config.PLACEHOLDER


def _not_done_clause(not_done: str) -> str:
    items = [x.strip() for x in re.split(r",|;| and ", not_done or "") if x.strip()]
    verbs = []
    for it in items:
        low = it.lower()
        if low in ("attic", "the attic"):
            verbs.append("inspect the attic")
        elif low in ("water test", "a water test"):
            verbs.append("do a water test")
        else:
            verbs.append("check " + it)
    if not verbs:
        return ""
    joined = verbs[0] if len(verbs) == 1 else ", ".join(verbs[:-1]) + " or " + verbs[-1]
    return "We did not %s, and we" % esc(joined)


def page1(s: Spec, f: Facts, imgs: dict, has_options: bool) -> str:
    if s.badge.severity == "hide":
        badge = ""
    else:
        label, cls = SEV[s.badge.severity]
        badge = '<div class="badge%s"><b>%s</b><p>%s</p></div>\n' % (
            "" if cls == "red" else " " + cls, label, rich(s.badge.text))
    meta = [
        '<div><span>Customer:</span> %s</div><div><span>Job:</span> #%s</div>'
        '<div><span>Date:</span> %s</div>' % (_ph(f.customer), esc(f.job_number),
                                              _date_long(f.date)),
        '<div style="grid-column:span 2"><span>Property:</span> %s</div>'
        '<div><span>Weather:</span> %s</div>' % (_ph(f.address), esc(s.weather)),
    ]
    inspector = s.inspector or f.inspector          # Owen's choice wins over Zuper's (2026-11-12)
    if inspector:
        meta.append('<div><span>Inspector:</span> %s</div>' % esc(inspector))
    if s.roof:
        meta.append('<div style="grid-column:span 2"><span>Roof:</span> %s</div>' % esc(s.roof))
    how = s.inspected_how.rstrip()
    how = esc(how) + ("" if not how or how[-1] in ".!?" else ".")
    nd = (" <b>Not done:</b> %s." % esc(s.not_done.rstrip("."))) if s.not_done else ""
    meta.append('<div style="grid-column:1/-1"><span>How we inspected:</span> %s%s</div>'
                % (how, nd))
    figs = ['<figure>%s<figcaption>%s</figcaption></figure>' % (
        _img(s.hero.cause_photo, s.hero.captions[0].rstrip("."), imgs), esc(s.hero.captions[0]))]
    if s.hero.inside_photo:
        figs.append('<figure>%s<figcaption>%s</figcaption></figure>' % (
            _img(s.hero.inside_photo, s.hero.captions[1].rstrip("."), imgs),
            esc(s.hero.captions[1])))
    sev = s.badge.severity
    if sev == "hide":                   # no badge: the call to action follows the worst finding
        found = {fd.severity for fd in s.findings}
        sev = next((x for x in ("now", "soon", "watch") if x in found), "none")
    if sev in ("now", "soon"):
        when = ("<b>within the next 2 weeks</b> — every rain adds more water to the wood and the "
                "ceiling" if sev == "now" else "<b>in the next few months</b>")
        if has_options:
            cta = ('<b>What to do next:</b> choose a repair option in <b>"Your repair options"</b> '
                   '(near the end). We recommend fixing this %s. Until then, see <b>"Until '
                   'it\'s fixed"</b> right after the options.' % when)
        else:
            cta = ('<b>What to do next:</b> review the repair proposal we send you. We recommend fixing '
                   'this %s. Until then, see <b>"Until it\'s fixed"</b> near the end.' % when)
    else:
        cta = ("<b>What to do next:</b> nothing urgent. We recommend checking these items at your "
               "next service visit.")
    if s.next_steps:
        cta += " " + rich(s.next_steps)
    # No draft banner and no "[placeholder]" text in the customer's copy (owner, 2026-11-09):
    # a missing value is left out; the warnings go to Owen in the chat only.
    contact = "<br>".join(x for x in (
        ("Phone: " + esc(f.phone)) if f.phone else "",
        ("License #: " + esc(f.license)) if f.license else "",
        ("Office: " + esc(f.city)) if f.city else "") if x)
    return """
<div class="top">
 <img class="logo" src="{logo}" alt="Dream Team Roofing">
 <div class="brandtxt"><b>Dream Team Roofing</b><span>Roof Inspection Report</span></div>
 <small>{contact}</small>
</div>
<div class="meta">
 {meta}
</div>
{badge}<p class="legend"><i class="r">Fix now</i> = water is getting in · <i class="o">Fix soon</i> = fix in the next few months ·
<i class="b">Watch</i> = check at your next service visit</p>
<p class="what"><b>What happened.</b> {what}</p>
<div class="hero">
 {figs}
</div>
<div class="cta">{cta}</div>
<p class="tip">Tip: click or tap any photo to zoom in.</p>
""".format(logo=logo_uri(), contact=contact,
           meta="\n ".join(meta), badge=badge, what=rich(s.what_happened),
           figs="\n ".join(figs), cta=cta)


def page2(s: Spec, imgs: dict) -> str:
    attic = "attic" in (s.not_done or "").lower()
    travel = ('<b>How the water travels.</b> Water that gets past the tiles runs <b>downhill along '
              'the wood</b> before it drips. That is why a stain can show up a few feet away from '
              'where the water got in.' + (" We did not go into the attic on this visit; we'll "
                                           "check the wood underneath when we open the area."
                                           if attic else ""))
    label = (s.location.entry_label if s.location and s.location.entry_label
             else "Water gets in at the opening")
    water = _asset("water.svg").replace(WATER_DEFAULT_LABEL, esc(label))
    if s.location:
        loc = s.location
        cap = loc.caption or "Photo %s — where it is on your roof." % loc.photo.num
        where = """<div class="loc">
 <div>{img}
  <p class="refs">{cap}</p></div>
 <div><p style="font-size:10.5pt;margin:0"><b>Where it is.</b> {where}</p>
  <p style="font-size:10.5pt;margin:6px 0 0">{travel}</p></div>
</div>""".format(img=_img(loc.photo, cap.rstrip("."), imgs), cap=esc(cap), where=rich(loc.where_text),
                 travel=travel)
    else:
        where = '<p style="font-size:10.5pt;margin:0">%s</p>' % travel
    words = s.words if s.words is not None else [Word(term=t, text=x) for t, x in DEFAULT_WORDS]
    wtxt = " ".join("<b>%s</b> — %s" % (esc(w.term), esc(w.text)) for w in words)
    return f"""
<h2 class="pgbreak" style="margin-top:0">Where it is and how the water travels</h2>
{where}
<div class="diagram" style="margin-top:3mm">{water}</div>
<div class="two" style="margin-top:5mm"><div><h2 style="margin-top:0">How your roof keeps water out</h2><ul class="chk">
  <li><b>Tiles</b> are like fish scales: they overlap to shed most of the rain.</li>
  <li><b>Underlayment</b> is like a raincoat under your jacket: the real waterproof layer.</li>
  <li><b>Roof deck</b> (the wood) is like the walls of a tent: it holds everything up.</li>
  <li><b>Flashing</b> is like a raincoat's collar and cuffs: it seals the edges and walls.</li>
  <li><b>Mortar</b> on caps is like grout in a shower: it seals the joints.</li></ul></div>
 <div><h2 style="margin-top:0">Words to know</h2>
 <p style="font-size:9.6pt;margin:0">{wtxt}</p></div></div>
"""


def inside_block(ins: Inside, imgs: dict) -> str:
    items = "\n".join('  <li><span class="%s">%s</span> — %s</li>' % (
        CHECK[i.state][0], CHECK[i.state][1], rich(i.text)) for i in ins.items)
    head = '<h2 style="margin-top:4mm">%s</h2>' % esc(ins.title)
    if not ins.photo:
        return '%s\n<ul class="chk">\n%s\n</ul>\n' % (head, items)
    cap = ins.caption or "Photo %s." % ins.photo.num
    return """{head}
<div class="two inside">
 <div>{img}
  <p class="refs">{cap}</p></div>
 <ul class="chk">
{items}
 </ul>
</div>
""".format(head=head, img=_img(ins.photo, cap.rstrip("."), imgs), cap=esc(cap), items=items)


def findings_html(s: Spec, imgs: dict) -> str:
    parts = ['<div class="pgbreak"></div><h2 style="margin-top:0">What we found</h2>\n']
    after = (s.inside.after_finding or len(s.findings)) if s.inside else None
    for n, fd in enumerate(s.findings, 1):
        parts.append(card(n, fd, imgs))
        if after is not None and n == min(after, len(s.findings)):
            parts.append(inside_block(s.inside, imgs))
    return "".join(parts)


def _payment(o: Option) -> str:
    if o.deposit is None or o.total is None:
        return DASH
    if o.deposit <= 0:
        return "Paid when done"
    rest = o.total - o.deposit
    if o.total > 0 and abs(o.deposit * 2 - o.total) < 0.01:
        return "50%% to start (%s), 50%% when done" % fmt_money(o.deposit)
    return "%s to start, %s when done" % (fmt_money(o.deposit), fmt_money(rest))


def options_html(s: Spec, f: Facts) -> str:
    opts = f.options
    if not opts:
        return ('<div class="pgbreak"></div><h2 style="margin-top:0">Your repair options</h2>\n'
                '<p style="font-size:10pt">We will send your repair options separately.</p>\n')
    texts = {k.strip().upper(): v for k, v in (s.option_text or {}).items()}

    def td(o: Option, inner: str, extra: str = "") -> str:
        cls = " ".join(x for x in (("rec" if o.recommended else ""), extra) if x)
        return '<td%s>%s</td>' % (' class="%s"' % cls if cls else "", inner)

    head = "".join('<th%s>%s</th>' % (' class="rec"' if o.recommended else "",
                                      esc(o.name.title()) + (" — recommended" if o.recommended
                                                             else "")) for o in opts)
    rows = [" <tr><th></th>%s</tr>" % head]

    def row(title: str, cells: list[str], cls: str = "") -> None:
        rows.append(' <tr%s><th>%s</th>%s</tr>' % (' class="%s"' % cls if cls else "", title,
                                                   "".join(cells)))

    fixes = [rich(texts[o.name.upper()].fixes) if o.name.upper() in texts and
             texts[o.name.upper()].fixes else esc(o.description or DASH) for o in opts]
    if any(x != DASH for x in fixes):
        row("What it fixes", [td(o, x) for o, x in zip(opts, fixes, strict=True)])
    incl = [rich(texts[o.name.upper()].includes) if o.name.upper() in texts and
            texts[o.name.upper()].includes else (esc(" · ".join(o.steps)) or DASH) for o in opts]
    if any(x != DASH for x in incl):
        row("Includes", [td(o, x) for o, x in zip(opts, incl, strict=True)])
    row("Warranty (workmanship)", [td(o, esc(o.warranty or DASH)) for o in opts])
    if any(o.retail is not None for o in opts):
        row("Regular price", [td(o, fmt_money(o.retail), "strike") for o in opts])
    if any(o.member is not None for o in opts):
        row("AHS member price", [td(o, fmt_money(o.member)) for o in opts])
    if any(o.credit is not None for o in opts):
        row("AHS credit", [td(o, fmt_money(o.credit, neg=True)) for o in opts])
    row("You pay", [td(o, "<b>%s</b>" % fmt_money(o.total if o.total is not None
                                                   else o.customer_pays)) for o in opts], "pay")
    row("Payment", [td(o, _payment(o)) for o in opts])
    for name in texts:
        if name not in {o.name.upper() for o in opts}:
            f.warnings.append("option_text names %s, which %s does not have." % (name, f.proposal))
    credits = {o.credit for o in opts if o.credit is not None}
    note = []
    if len(credits) == 1:
        note.append("The <b>%s credit</b> is what AHS authorized for this claim (proposal %s)."
                    % (fmt_money(abs(credits.pop())), esc(f.proposal)))
    if s.options_note:
        note.append(rich(s.options_note))
    out = ('<div class="pgbreak"></div><h2 style="margin-top:0">Your repair options</h2>\n'
           '<table class="t">\n%s\n</table>\n' % "\n".join(rows))
    if note:
        out += '<p style="font-size:9pt;margin:4px 0">%s</p>\n' % " ".join(note)
    return out


def page4(s: Spec, f: Facts) -> str:
    until = s.until_fixed or DEFAULT_UNTIL
    lis = "\n".join("  <li>%s</li>" % esc(x) for x in until)
    ahs = any(o.credit is not None for o in f.options)
    ahs_txt = ("<b>About AHS.</b> Whether AHS pays for part of a repair depends on your AHS "
               "contract and AHS's decision. We cannot promise coverage beyond what AHS "
               "authorizes.<br>\n" if ahs else "")
    day = _date_short(f.date)
    on = ("on %s" % day) if day else "on the day of our visit"
    nd = _not_done_clause(s.not_done)
    walls = ("%s could not see inside walls or under tiles we did not remove." % nd) if nd else \
        "We could not see inside walls or under tiles we did not remove."
    # No "Your repair options" table and no signature line in the customer's copy (owner,
    # 2026-11-09): the proposal goes to the customer on its own. options_html stays for reuse.
    return f"""<div style="margin-top:6mm"><h2 style="margin-top:0">Until it's fixed</h2><ol class="chk">
{lis}</ol></div>
<div class="legal">{ahs_txt}<b>About this report.</b> This was a visual inspection of the areas we could safely reach {on}. {walls} Some leaks only happen in certain
weather. This report describes what we found that day; it is not a promise that the roof will never leak. Anything new we find
during the repair will be shown to you with photos before extra work is done.</div>
"""


# --- the Good / Better / Best pages (2026-11-12)

TIER_RGB = {"GOOD": (96, 116, 150), "BETTER": (196, 146, 18), "BEST": (122, 62, 168)}
EXCLUDED_RGB = (235, 238, 242)
GBB_CSS = (
    ".keep{break-inside:avoid;page-break-inside:avoid}.gal figure,.gcov figure{"
    "break-inside:avoid}"
    ".gal{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:2mm 0 4mm}"
    ".gal figure{margin:0}.gal img{width:100%;height:150px;object-fit:cover;border-radius:6px}"
    ".gal figcaption{font-size:8.6pt;color:var(--mut);margin-top:2px}"
    ".gbb{border-top:6px solid var(--acc);padding-top:3mm}"
    ".ghead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;"
    "gap:4px 16px;margin-bottom:3mm}.gname{font-size:26pt;font-weight:900;color:var(--acc);"
    "line-height:1}.gsub{font-size:12pt;font-weight:700;flex:1 1 50%}"
    ".gmeta{font-size:10pt;color:var(--mut)}.gmeta b{color:var(--ink)}"
    ".ginfo{width:100%;border:1px solid var(--line);border-radius:6px}"
    ".gmissing{border:1px dashed var(--line);border-radius:6px;padding:10mm;text-align:center;"
    "color:var(--mut)}.gbb h3{font-size:12pt;color:var(--brand);margin:4mm 0 2mm}"
    ".gcov{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.gcov.n1{"
    "grid-template-columns:1fr}.gcov figure{margin:0}.gcov img{width:100%;border-radius:6px;"
    "border:3px solid var(--acc)}.gcov figcaption{font-size:8.6pt;color:var(--mut)}"
    ".gscope{margin:0;padding-left:5mm}.gscope li{font-size:10pt;margin:0 0 4px}"
    ".gintro{font-size:10.5pt;margin:0 0 3mm}"
    ".gprice{margin-left:auto;text-align:right;font-size:10pt;line-height:1.35}"
    ".gprice .strike{text-decoration:line-through;color:var(--red)}"
    ".gprice .gcredit{color:var(--green)}.gprice .gpay{font-size:16pt;color:var(--acc)}"
    ".gprice .gterms{color:var(--mut);font-size:9pt}"
    "table.cmp tr.gpayrow td{font-size:13pt;font-weight:900}"
    "table.cmp{width:100%;border-collapse:collapse;font-size:10.5pt}"
    "table.cmp th,table.cmp td{padding:7px 9px;border-bottom:1px solid var(--line);"
    "text-align:center}table.cmp td:first-child{text-align:left;font-weight:700;"
    "color:var(--brand)}table.cmp th{font-size:13pt;font-weight:900}"
    "@media(max-width:640px){.gal{grid-template-columns:repeat(2,1fr)}.gcov{"
    "grid-template-columns:1fr}}")
GBB_DEFAULT_INTRO = ("Here are three ways to fix it. <b>Every option stops the leak.</b> Better "
                     "and Best add more: more of the roof repaired, stronger materials and a "
                     "longer warranty. We will send the prices once AHS tells us how much it "
                     "covers.")
GBB_PRICED_INTRO = ("Here are three ways to fix it. <b>Every option stops the leak.</b> Better "
                    "and Best add more: more of the roof repaired, stronger materials and a "
                    "longer warranty. Each option shows what AHS covers and what you pay.")


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def draw_coverage(cov: Coverage, rgb: tuple[int, int, int], crop_gps: bool | None = None) -> str:
    """The real photo with each area filled in the option's colour (or dashed grey for work
    the option leaves alone), labelled. Points are fractions of the ORIGINAL picture."""
    from PIL import Image, ImageDraw
    p = cov.photo
    with Image.open(p.path) as src:
        base = src.convert("RGBA")
    w, h = base.size
    k = max(w, h) / 1024

    def s(n: float) -> int:
        return max(1, round(n * k))
    fill = Image.new("RGBA", base.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill)
    polys = [([(x * w, y * h) for x, y in a.points], a) for a in cov.areas]
    for pts, a in polys:
        if a.kind == "covered":
            fd.polygon(pts, fill=(*rgb, 95))
    im = Image.alpha_composite(base, fill)
    d = ImageDraw.Draw(im)
    for pts, a in polys:
        ring = [*pts, pts[0]]
        if a.kind == "covered":
            d.line(ring, fill=(*rgb, 255), width=s(6), joint="curve")
        else:
            for (x0, y0), (x1, y1) in itertools.pairwise(ring):
                n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 // s(18)))
                for i in range(0, n, 2):
                    d.line([(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n),
                            (x0 + (x1 - x0) * (i + 1) / n, y0 + (y1 - y0) * (i + 1) / n)],
                           fill=(*EXCLUDED_RGB, 255), width=s(5))
    f = _font(s(26))
    for pts, a in polys:
        if not a.label:
            continue
        col = rgb if a.kind == "covered" else (120, 128, 140)
        tw = d.textlength(a.label, font=f)
        cx = sum(x for x, _ in pts) / len(pts)
        y = max(y for _, y in pts) + s(8)
        x = min(max(s(4), cx - tw / 2 - s(12)), w - tw - s(28))
        y = min(y, h - s(50))
        d.rounded_rectangle((x, y, x + tw + s(24), y + s(42)), radius=s(10),
                            fill=(255, 255, 255, 240), outline=(*col, 255), width=s(4))
        d.text((x + s(12), y + s(6)), a.label, fill=(22, 24, 33), font=f)
    crop = s(70) if (p.crop_gps if crop_gps is None else crop_gps) else 0
    out = im.convert("RGB").crop((0, 0, w, h - crop))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=84)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _image_uri(path: Path, max_side: int = 1600) -> str:
    from PIL import Image
    with Image.open(path) as src:
        im = src.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def galleries_html(s: Spec, imgs: dict) -> str:
    out = []
    for g in s.galleries or []:
        figs = "".join('<figure>%s<figcaption>%s</figcaption></figure>' % (
            _img(gp.photo, gp.caption or g.title, imgs), esc(gp.caption)) for gp in g.photos)
        out.append('<div class="keep"><h2 style="margin-top:6mm">%s</h2>\n<div class="gal">%s'
                   '</div></div>\n' % (esc(g.title), figs))
    return "".join(out)


def option_fact(o: OptionBlock, f: Facts) -> Option | None:
    return next((x for x in f.options if x.name.strip().upper() == o.name), None)


def option_warranty(o: OptionBlock, f: Facts) -> str:
    fact = option_fact(o, f)
    return (fact.warranty if fact and fact.warranty else "") or o.warranty


def you_pay(x: Option) -> float | None:
    return x.total if x.total is not None else x.customer_pays


def price_box(o: OptionBlock, f: Facts) -> str:
    """The option's prices, READ from the proposal (Facts), never typed in the spec. A figure
    the proposal does not give is a dash, never a guess."""
    x = option_fact(o, f)
    if x is None:
        f.warnings.append("%s: the proposal%s has no %s option, so its prices are not shown."
                          % (o.name, " " + f.proposal if f.proposal else "", o.name))
        return ""
    rows = []
    if x.retail is not None:
        rows.append('<span class="strike">%s retail</span>' % fmt_money(x.retail))
    if x.member is not None:
        rows.append("AHS member price %s" % fmt_money(x.member))
    if x.credit is not None:
        rows.append('<span class="gcredit">AHS credit %s</span>' % fmt_money(x.credit, neg=True))
    rows.append('<b class="gpay">You pay %s</b>' % fmt_money(you_pay(x)))
    pay = _payment(x)
    if pay != DASH:
        rows.append('<span class="gterms">%s</span>' % esc(pay))
    return '<div class="gprice">%s</div>' % "".join("<div>%s</div>" % r for r in rows)


def gbb_html(s: Spec, f: Facts, pictures: dict[str, Path | None]) -> str:
    if not s.gbb:
        return ""
    g = s.gbb
    priced = g.show_prices and bool(f.options)
    if g.show_prices and not f.options:
        f.warnings.append("Prices are switched on but there is no proposal with options to read "
                          "them from, so none are shown.")
    parts = ['<div class="pgbreak"></div><h2 style="margin-top:0">Your repair options</h2>\n'
             '<p class="gintro">%s</p>\n' % rich(
                 g.intro or (GBB_PRICED_INTRO if priced else GBB_DEFAULT_INTRO))]
    for i, o in enumerate(g.options):
        rgb = TIER_RGB[o.name]
        pic = pictures.get(o.name)
        img = ('<img class="zoom ginfo" src="%s" alt="%s">' % (_image_uri(pic), esc(o.headline))
               if pic else "")
        war = option_warranty(o, f)
        meta = " · ".join(x for x in (("<b>Warranty:</b> " + esc(war)) if war else "",
                                      ("<b>Time on site:</b> " + esc(o.days)) if o.days else "")
                          if x)
        covs = "".join('<figure><img class="zoom" src="%s" alt="%s"><figcaption>%s</figcaption>'
                       '</figure>' % (draw_coverage(c, rgb), esc(c.caption or "Area covered"),
                                      esc(c.caption)) for c in o.coverage)
        cov = ('<div class="keep"><h3>Area covered</h3><div class="gcov n%d">%s</div></div>'
               % (len(o.coverage), covs) if o.coverage else "")
        scope = "".join("<li>%s</li>" % rich(x) for x in o.scope)
        parts.append(
            '%s<section class="gbb" style="--acc:%s"><div class="ghead"><span class="gname">%s'
            '</span><span class="gsub">%s</span><span class="gmeta">%s</span>%s</div>%s%s'
            '<div class="keep"><h3>Scope of work</h3><ul class="gscope">%s</ul></div>'
            '</section>\n' % (
                '<div class="pgbreak"></div>' if i else "", _hex(rgb), esc(o.name),
                esc(o.headline), meta, price_box(o, f) if priced else "", img, cov, scope))
    if g.compare or priced:
        names = [o.name for o in g.options]
        head = "".join('<th style="color:%s">%s</th>' % (_hex(TIER_RGB[n]), n) for n in names)
        rows = "".join("<tr><td>%s</td>%s</tr>" % (esc(r.label), "".join(
            "<td>%s</td>" % esc(getattr(r, n)) for n in names)) for r in g.compare)
        if priced:
            fx = {o.name: option_fact(o, f) for o in g.options}
            rows += '<tr class="gpayrow"><td>You pay</td>%s</tr>' % "".join(
                "<td>%s</td>" % (fmt_money(you_pay(fx[n])) if fx[n] else DASH) for n in names)
        parts.append('<div class="pgbreak"></div><h2 style="margin-top:0">Compare the options'
                     '</h2>\n<table class="cmp"><tr><th></th>%s</tr>%s</table>\n' % (head, rows))
    return "".join(parts)


def render(spec: Spec | dict, photos_dir: Path | str | None, facts: Facts,
           pictures: dict[str, Path | None] | None = None,
           roots: list[Path | str] | None = None) -> str:
    """Pure: the spec + the fetched facts -> one self-contained HTML page (photos and logo
    embedded, the zoom viewer inline). `photos_dir` (or `roots`) bounds where photo paths may
    point; `pictures` = each option's AI picture file (saved_reports.py), None = not made."""
    s = validate(spec, photos_dir, roots)
    imgs: dict[str, str] = {}
    body = page1(s, facts, imgs, bool(s.gbb)) + page2(s, imgs) + findings_html(s, imgs) \
        + galleries_html(s, imgs) + gbb_html(s, facts, pictures or {}) + page4(s, facts)
    title = "Roof Inspection Report — Job %s" % esc(facts.job_number)
    return ("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s%s%s</style><body>%s%s</body></html>") % (
        title, _asset("report.css"), EXTRA_CSS, GBB_CSS, body, _asset("lightbox.html"))


def facts_to_dict(f: Facts) -> dict:
    """A JSON copy of the facts, kept with a saved report so a later edit renders without
    asking Zuper again."""
    from dataclasses import asdict
    d = asdict(f)
    d["date"] = f.date.isoformat() if f.date else None
    d.pop("warnings", None)
    return d


def facts_from_dict(d: dict) -> Facts:
    d = dict(d or {})
    opts = [Option(**{k: v for k, v in o.items() if k in Option.__dataclass_fields__})
            for o in d.pop("options", None) or []]
    date = d.pop("date", None)
    f = Facts(**{k: v for k, v in d.items() if k in Facts.__dataclass_fields__}, options=opts)
    if date:
        f.date = datetime.fromisoformat(date)
    return f


# ------------------------------------------------------------------------------ files

def make_pdf(html_path: Path, pdf_path: Path, browser: str | None = None) -> str | None:
    """Print the SAME html headless. Returns None when the PDF was made, else why not."""
    browser = browser or config.chromium()
    if not browser:
        return ("no Chromium on this server (OWEN_AI_CHROMIUM / chromium on PATH), so only the "
                ".html was made; it prints to PDF from any browser")
    profile = tempfile.mkdtemp(prefix="owen-ai-pdf-")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           "--print-to-pdf-no-header", "--disable-dev-shm-usage", "--no-first-run",
           "--user-data-dir=" + profile, "--print-to-pdf=" + str(pdf_path)]
    if sys.platform != "win32" and hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.append("--no-sandbox")            # Chromium refuses to run as root otherwise
    cmd.append(html_path.resolve().as_uri())
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=PDF_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        with contextlib.suppress(OSError):
            pdf_path.unlink()
        return "the PDF printer failed (%s); only the .html was made" % type(exc).__name__
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        return "the PDF printer wrote nothing; only the .html was made"
    return None


def cleanup(root: Path, now: float | None = None) -> int:
    now = now or time.time()
    removed = 0
    if not root.is_dir():
        return 0
    for d in root.iterdir():
        if d.is_dir() and ID_RE.fullmatch(d.name) and \
                now - d.stat().st_mtime > config.REPORT_KEEP_DAYS * 86400:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def friendly_name(meta: dict, file: str) -> str:
    last = re.sub(r"[^A-Za-z0-9 '\-]", "", str(meta.get("last_name") or "")).strip()
    base = "Inspection Report - Job %s" % re.sub(r"[^A-Za-z0-9]", "", str(meta.get("job_number")))
    return "%s%s.%s" % (base, " - " + last if last else "", file.rsplit(".", 1)[1])


def read_meta(report_id: str) -> dict | None:
    if not ID_RE.fullmatch(report_id or ""):
        return None
    with contextlib.suppress(OSError, ValueError):
        return json.loads((config.report_dir() / report_id / "meta.json").read_text("utf-8"))
    return None


def file_path(report_id: str, file: str) -> Path | None:
    """The file to serve, or None. Only the two names, only a well-formed id, only inside."""
    if file not in FILES or not ID_RE.fullmatch(report_id or ""):
        return None
    root = config.report_dir().resolve()
    p = (root / report_id / file).resolve()
    return p if p.is_relative_to(root) and p.is_file() else None


def urls(report_id: str) -> tuple[str, str]:
    return (URL_PREFIX + report_id + "/report.html", URL_PREFIX + report_id + "/report.pdf")


def list_reports(job_uid: str | None = None) -> list[dict]:
    root = config.report_dir()
    out = []
    if root.is_dir():
        for d in root.iterdir():
            m = read_meta(d.name) if d.is_dir() else None
            if not m or (job_uid and m.get("job_uid") != job_uid):
                continue
            h, p = urls(d.name)
            out.append({**{k: m.get(k) for k in ("id", "job_uid", "job_number", "customer",
                                                  "proposal", "created_at", "final")},
                        "html_url": h, "pdf_url": p if (d / "report.pdf").is_file() else None})
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


def write(spec: Any, now: datetime | None = None, facts: Facts | None = None) -> dict:
    """Validate, fetch the facts (unless given), render, save, print. Raises SpecError,
    LookupError or ZuperError."""
    photos_dir = config.photo_dir()
    s = validate(spec, photos_dir)
    f = facts or fetch_facts(s.job_uid, s.proposal)
    page = render(s, photos_dir, f)
    root = config.report_dir()
    root.mkdir(parents=True, exist_ok=True)
    cleanup(root)
    at = (now or datetime.now(UTC)).astimezone(TZ)
    num = re.sub(r"[^A-Za-z0-9]", "", f.job_number)[:20] or "job"
    while True:
        rid = "%s-%s" % (num, at.strftime("%Y%m%d-%H%M%S"))
        d = root / rid
        try:
            d.mkdir()
            break
        except FileExistsError:
            at += timedelta(seconds=1)
    (d / "report.html").write_text(page, "utf-8")
    why = make_pdf(d / "report.html", d / "report.pdf")
    warnings = list(f.warnings)
    if why:
        warnings.append("PDF not made: " + why + ".")
    meta = {"id": rid, "job_uid": f.job_uid, "job_number": f.job_number,
            "customer": f.customer, "last_name": f.last_name, "proposal": f.proposal,
            "created_at": at.isoformat(), "final": s.final, "pdf": why is None,
            "warnings": warnings}
    (d / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), "utf-8")
    h, p = urls(rid)
    return {"id": rid, "html_path": str(d / "report.html"),
            "pdf_path": str(d / "report.pdf") if why is None else None,
            "html_url": h, "pdf_url": p if why is None else None, "warnings": warnings,
            "final": s.final, "job_number": f.job_number, "customer": f.customer,
            "proposal": f.proposal}


# ------------------------------------------------------------------------------ the tool

def make_inspection_report(args: dict) -> str:
    try:
        out = write(args)
    except SpecError as exc:
        return ("ERROR: the spec is not valid — fix these and call again:\n"
                + "\n".join("- " + p for p in exc.problems))
    except LookupError as exc:
        return "ERROR: %s" % exc
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)
    lines = ["Report %s for job #%s%s%s — %s." % (
        out["id"], out["job_number"], ", " + out["customer"] if out["customer"] else "",
        ", options from " + out["proposal"] if out["proposal"] else "",
        "ready for Owen to review and download (nothing was sent)"),
        "Give Owen these links exactly as written (markdown):",
        "[Download report (.html)](%s)" % out["html_url"]]
    if out["pdf_url"]:
        lines.append("[Download report (.pdf)](%s)" % out["pdf_url"])
    if out["warnings"]:
        lines.append("Warnings (tell Owen):")
        lines += ["- " + w for w in out["warnings"]]
    lines.append("Nothing was sent to anyone. To change it, edit the spec and call again "
                 "(a new version with new links).")
    return "\n".join(lines)


DESCRIPTION = (
    "MAKE the customer inspection report for one job (Dream Team design: logo header, severity "
    "badge, annotated photos, finding cards, the proposal's options table, legal text) as a "
    "DRAFT for Owen to download — it is never sent to anyone. Follow the inspection-report "
    "skill. Input = the spec: job_uid; badge {severity now|soon|watch|none|hide, text} "
    "(hide = no badge at all - use it when Owen asks to remove the badge, never 'none'); "
    "what_happened; hero {cause_photo, inside_photo?, captions[1-2]}; location? {photo, "
    "where_text, caption?, entry_label?}; findings[1-8] {severity now|soon|watch, title, "
    "photos[1-2], refs, saw, means, why, like, fix, prevent}; inside? {title?, photo?, caption?, "
    "items[{state yes|no|not_checked, text}], after_finding?}; not_done ('attic, water test'); "
    "weather; inspected_how; optional roof, until_fixed[], words[{term,text}], option_text "
    "{GOOD: {fixes, includes}}, options_note, next_steps, proposal ('DTR-129'), final. "
    "A photo = {path: a `path` job_photos returned, num?: its number (default from the file "
    "name), marks[{shape ellipse|rect|line, box [x0,y0,x1,y1] as 0-1 FRACTIONS of the picture, "
    "color red|orange|blue|green, label <=24 chars}], brighten?}. Long text fields allow only "
    "<b> and <i>; everything is escaped. Do NOT type the customer, address, date, job number or "
    "any price: the tool reads them from Zuper and the proposal itself. Unknown keys, bad paths "
    "or boxes outside 0-1 are refused with a list of what to fix. Returns markdown download "
    "links (.html and .pdf) and warnings to pass on to Owen.")

SPECS = (("make_inspection_report", DESCRIPTION, input_schema(), make_inspection_report),)
