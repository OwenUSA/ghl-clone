"""Optional step pictures on a proposal Owen AI already created (2026-11-09, owner rules).

  * ONLY on a draft row whose status is "created" (approved by the owner and created in Zuper as
    a DRAFT), and only when the owner asks: the "Add step pictures" button on the Proposals tab,
    or Owen AI's `proposal_step_pictures` tool when he asks in the chat. Nothing is automatic.
  * generate -> the owner sees the banners -> "Put on the proposal" (attach) or "Discard".
    Nothing is ever sent to a customer: attach puts `option_image` on the DRAFT and stops.

One banner per option, 1536x512 (3:1), the "steps strip" the other session proved on TEST
DTR-155 (2026-11-07): a navy title bar; a left-to-right row of numbered step tiles with
captions — BETTER opens with a grey "1-N Everything in GOOD" tile and tags its own steps UPGRADE
(gold), BEST opens with "Everything in GOOD + BETTER" and tags PREMIUM (purple); a green "Final
result" tile; a bottom bar with the warranty badge and "Illustrations - they show the type of
work, not your home". The STEPS come from the real proposal: the bullets of each option's
`scope_html` on the draft row (BETTER / BEST: only the bullets the previous tier lacks), at most
6 per tier, captions of at most 5 words. ALL text is drawn here with Pillow; the AI picture has
no text at all.

The pictures: ONE OpenAI image call per option (gpt-image-2, quality high, 1536x1024, png) for a
grid of panels in a fixed order (the steps, then the finished result), cropped into the tiles.
The gutters are found by brightness near where they should be; when one is not found the cell
is cut at its expected place, so a tile is always produced. Cost (OpenAI's listed gpt-image-2
prices, the other session's measured usage: 5,488 output tokens per high 1536x1024 image) is
about $0.17 per option, ~$0.51 per Good/Better/Best proposal; each call's tokens and dollars
are logged and kept on the row. A failed or timed-out call -> a sentence, no banners kept, and
nothing reaches Zuper (generation never talks to Zuper at all).

The key: OWEN_AI_OPENAI_API_KEY; empty -> the first AI connection (Settings -> AI Connections)
of provider "openai" on OpenAI's own endpoint, decrypted with AI_SECRETS_KEY. Neither -> a
sentence. Server switch: OWEN_AI_PROPOSAL_PICTURES_ENABLED (default false), plus
OWEN_AI_PROPOSALS_ENABLED and the registry's owen_ai "zuper_proposals" level.

Attach (`client.owen_ai_proposal_pictures(estimate_uid)`, the row's own recorded uid): read the
proposal, upload each banner (POST /misc/upload), then ONE PUT /estimate/{uid} whose options are
the read-back options with only `option_image` added — checked here AND by the client before a
connection — then read it back: pictures present, totals unchanged, still DRAFT. Never retried.
"""
from __future__ import annotations

import base64
import copy
import io
import logging
import math
import re
import secrets
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..models import AiConnection, OwenAiProposalDraft
from ..zuper import client as zc
from ..zuper import config as zuper_config
from . import config, proposals

log = logging.getLogger("owen_ai")

MODEL = "gpt-image-2"
IMAGE_SIZE = "1536x1024"
QUALITY = "high"
TIMEOUT_SECONDS = 300.0
# US dollars per 1M tokens (OpenAI's listed gpt-image-2 prices; image input is an estimate).
PRICE_TEXT_IN, PRICE_IMAGE_IN, PRICE_OUT = 5.0, 8.0, 30.0
EST_COST_PER_OPTION = 0.17           # measured: 5,488 output tokens + ~450 text tokens
MAX_STEPS = 6
MAX_WORDS = 5
STALE = timedelta(minutes=20)        # a "generating" claim older than this was a crash
W, H = 1536, 512
STATUSES = ("generating", "ready", "attaching", "attached", "failed", "discarded")
NAME_RX = re.compile(r"^(good|better|best)-[0-9a-f]{8}\.jpg$")
DISCLAIMER = "Illustrations - they show the type of work, not your home"

NAVY = (14, 27, 62)
INK = (22, 24, 33)
GREEN = (24, 116, 46)
GOLD = (196, 146, 18)
PURPLE = (122, 62, 168)
GREY = (128, 128, 128)
LIGHT = (205, 208, 214)
ACCENT = {"GOOD": (96, 116, 150), "BETTER": GOLD, "BEST": PURPLE}
TAG = {"BETTER": ("UPGRADE", GOLD), "BEST": ("PREMIUM", PURPLE)}


class Refused(Exception):
    """Refused before anything was claimed, spent or sent (409 in the API)."""


class PlanError(Refused):
    """The proposal's scope gives no steps to draw."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


# ------------------------------------------------------------------------------ gates

def refusal(db: Session) -> str | None:
    """Why pictures may not be generated or attached now (both server switches + registry)."""
    if not zuper_config.owen_ai_proposal_pictures_enabled():
        return zuper_config.OWEN_AI_PROPOSAL_PICTURES_OFF_SENTENCE
    return proposals.refusal(db)


NO_KEY = ("There is no OpenAI key for step pictures: set OWEN_AI_OPENAI_API_KEY on the server or "
          "add an OpenAI connection in Settings -> AI Connections.")


def openai_key(db: Session) -> str | None:
    key = config.picture_openai_key()
    if key:
        return key
    conn = db.scalars(select(AiConnection).where(
        AiConnection.provider == AiConnection.OPENAI, AiConnection.base_url.is_(None))
        .order_by(AiConnection.id)).first()
    if conn is None:
        return None
    from ..ai import vault
    try:
        return vault.decrypt(conn.api_key_encrypted) or None
    except vault.SecretsUnavailable:
        return None


def estimate_usd(row: OwenAiProposalDraft) -> float:
    return round(EST_COST_PER_OPTION * len(row.options or []), 2)


# ------------------------------------------------------------------------------ the plan

_LI = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_P = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_SKIP = re.compile(
    r"^(scope of work|everything (in|from|included)|all (of )?(the )?(work )?in (good|better)|"
    r"includes? (everything|all)|same as (good|better)|plus:?$|workmanship warranty|warranty|"
    r"payment terms|deposit|regular price|your price|price|total|you save|savings|ahs )",
    re.IGNORECASE)
_ARTICLES = {"the", "a", "an", "any", "every", "all", "each", "our", "your", "its", "their",
             "nearby", "existing"}
# A caption is cut before one of these (at word 2 or later; "of" at word 3 or later).
_CUT = {"around", "at", "on", "in", "from", "above", "below", "beside", "near", "over", "under",
        "across", "to", "for", "with", "into", "onto", "along", "between", "where", "that",
        "which", "and", "then", "so", "found", "seen", "during", "if", "once", "as", "by",
        "throughout", "behind", "inside", "within", "up", "down"}


def _plain(fragment: str) -> str:
    return proposals.plain(fragment)


def bullets(scope_html: str | None) -> list[str]:
    """The scope's steps: its <li> items, else its paragraphs; price, warranty, payment and
    "everything in GOOD" lines are not steps."""
    html_ = scope_html or ""
    items = [_plain(x) for x in _LI.findall(html_)] or [_plain(x) for x in _P.findall(html_)]
    out = []
    for t in items:
        t = t.strip(" .;:-")
        if not t or "$" in t or _SKIP.match(t):
            continue
        out.append(t)
    return out


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def caption(text: str) -> str:
    """At most 5 words from one scope bullet: its first clause, without articles or -ly words,
    cut before the first preposition / conjunction that keeps at least two words."""
    t = re.split(r"[.;:(]| [-\u2013\u2014] |, (?:then|and|including|lapped|so)\b", text,
                 maxsplit=1)[0]
    t = re.sub(r"\s*/\s*", "/", t)                       # "patched / cracked" -> one word
    t = re.sub(r"\s*&\s*", "&", t)                       # "peel & stick" -> one word
    t = re.sub(r"\b(column|row|strip|piece|section)s? of\b", "", t, flags=re.IGNORECASE)
    words = [w.strip(",") for w in t.split()]
    words = [w for w in words if w and w.lower() not in _ARTICLES
             and not (len(w) > 4 and w.lower().endswith("ly") and w.lower() != "only")]
    cuts = [i for i, w in enumerate(words[:MAX_WORDS + 1])
            if (w.lower() in _CUT and i >= 2) or (w.lower() == "of" and i >= 3)]
    # Two words is thin ("Open roof"): take the next place to cut when there is one.
    cut = cuts[1] if len(cuts) > 1 and cuts[0] < 3 else (cuts[0] if cuts else None)
    if cut is not None:
        words = words[:cut]
    words = words[:MAX_WORDS]
    while words and words[-1].lower() in _CUT | {"of", "or"}:
        words.pop()
    out = " ".join(words).replace("&", " & ")
    return (out[:1].upper() + out[1:]) if out else ""


def roof_kind(row: OwenAiProposalDraft) -> str:
    if row.template_key in ("tile", "flat"):
        return row.template_key
    text = " ".join([row.title or ""] + [o.get("scope_html") or "" for o in row.options or []])
    text = text.lower()
    if "shingle" in text:
        return "shingle"
    if re.search(r"\bflat\b|low[- ]slope|modified bitumen|\btpo\b|built[- ]up", text):
        return "flat"
    if "metal" in text:
        return "metal"
    return "tile"


def warranty_badge(warranty: str | None) -> str:
    t = re.sub(r"^\s*workmanship\s+warranty\s*:?\s*", "", _plain(warranty or ""),
               flags=re.IGNORECASE)
    m = re.search(r"(\d+)\s*-?\s*(day|month|year|yr|mo)s?\b", t, re.IGNORECASE)
    if m:
        unit = {"yr": "year", "mo": "month"}.get(m.group(2).lower(), m.group(2).lower())
        return "%s-%s warranty" % (m.group(1), unit)
    if "lifetime" in t.lower():
        return "Lifetime warranty"
    t = t.strip(" .")
    return ("Warranty: " + (t if len(t) <= 22 else t[:21].rstrip() + "…")) if t else ""


def plan(row: OwenAiProposalDraft) -> list[dict]:
    """Per option, in GOOD / BETTER / BEST order: title, first tile, steps, tag, warranty."""
    opts = sorted(row.options or [], key=lambda o: proposals.OPTION_NAMES.index(o["name"]))
    if not opts:
        raise PlanError("This draft has no options.")
    seen: set[str] = set()
    done = 0
    prev: list[str] = []
    out = []
    for o in opts:
        mine = [b for b in bullets(o.get("scope_html")) if _norm(b) not in seen]
        for b in bullets(o.get("scope_html")):
            seen.add(_norm(b))
        steps = []
        for b in mine:
            c = caption(b)
            if c and _norm(c) not in {_norm(s["caption"]) for s in steps}:
                steps.append({"caption": c, "detail": b[:220]})
            if len(steps) == MAX_STEPS:
                break
        if not steps:
            raise PlanError("%s's scope has no step %s — nothing to draw." % (
                o["name"], "the earlier options lack" if prev else "in its bullets"))
        name = o["name"]
        if prev:
            title = "%s - Everything in %s + %s" % (
                name, prev[-1], "premium upgrades" if name == "BEST" else "upgrades")
            first = {"label": "1-%d" % done if done > 1 else "1",
                     "caption": "Everything in " + " + ".join(prev)}
        else:
            desc = _plain(o.get("option_description") or "").rstrip(" .")
            title = "%s - %s" % (name, desc) if desc else name
            first = None
        out.append({"name": name, "title": title, "first": first, "start": done + 1,
                    "steps": steps, "tag": TAG.get(name) if prev else None,
                    "warranty": warranty_badge(o.get("warranty")), "roof": roof_kind(row)})
        done += len(steps)
        prev.append(name)
    return out


# ------------------------------------------------------------------------------ the prompt

ROOF = {
    "tile": "a two-story Florida house with a terracotta concrete barrel (S-shaped) tile roof",
    "flat": "a Florida house with a flat, low-slope roof covered in grey modified-bitumen membrane",
    "shingle": "a Florida house with an asphalt architectural shingle roof",
    "metal": "a Florida house with a standing-seam metal roof",
}
FINISHED = {
    "tile": "the finished result: the repaired area with tiles neatly reset in straight rows, "
            "clean and watertight",
    "flat": "the finished result: the repaired flat roof area smooth, sealed and watertight",
    "shingle": "the finished result: the repaired area with new shingles lying flat in straight "
               "rows, clean and watertight",
    "metal": "the finished result: the repaired metal roof area with panels sealed, clean and "
             "watertight",
}


def grid(n_panels: int) -> tuple[int, int]:
    return (1, n_panels) if n_panels <= 3 else (2, math.ceil(n_panels / 2))


def prompt(p: dict) -> str:
    panels = [s["detail"] for s in p["steps"]] + [FINISHED[p["roof"]]]
    rows, cols = grid(len(panels))
    if rows * cols > len(panels):
        panels.append("the same finished roof seen from a little farther away")
    lines = "\n".join("(%d) %s" % (i + 1, t) for i, t in enumerate(panels))
    return (
        "A clean illustrative grid of exactly %d equal panels, %d row%s by %d columns, separated "
        "by thin pure-white gutters, with a thin white margin around the whole grid. One "
        "consistent, realistic illustration style in every panel: the same house, the same roof, "
        "the same daylight. The house: %s. Each panel is a close view of one stage of the roof "
        "work, shown by the roof itself and the materials (gloved hands are fine). Panels in "
        "this exact order, left to right, top row first:\n%s\n"
        "ABSOLUTELY NO TEXT anywhere in the image: no words, no letters, no numbers, no labels, "
        "no captions, no arrows with writing, no signs, no logos, no watermarks."
        % (rows * cols, rows, "" if rows == 1 else "s", cols, ROOF[p["roof"]], lines))


# ------------------------------------------------------------------------------ OpenAI

class ImageError(Exception):
    pass


def cost_usd(usage: dict | None) -> float:
    u = usage or {}
    ind = u.get("input_tokens_details") or {}
    if ind:
        text_in, image_in = ind.get("text_tokens") or 0, ind.get("image_tokens") or 0
    else:
        text_in, image_in = u.get("input_tokens") or 0, 0
    out = u.get("output_tokens", 0) or 0
    return round((text_in * PRICE_TEXT_IN + image_in * PRICE_IMAGE_IN + out * PRICE_OUT) / 1e6, 4)


def generate_image(key: str, text: str) -> tuple[bytes, dict]:
    """One image. No automatic retry: every call is money."""
    import openai

    from ..ai import providers
    http = {"http_client": providers.HTTP_CLIENT_FACTORY()} if providers.HTTP_CLIENT_FACTORY \
        else {}
    client = openai.OpenAI(api_key=key, base_url=providers.OPENAI_BASE_URL,
                           timeout=TIMEOUT_SECONDS, max_retries=0, **http)
    try:
        r = client.images.generate(model=MODEL, prompt=text, n=1, size=IMAGE_SIZE,
                                   quality=QUALITY, output_format="png")
    except openai.APITimeoutError:
        raise ImageError("OpenAI did not answer within %d seconds." % TIMEOUT_SECONDS) from None
    except openai.APIStatusError as e:
        msg = ""
        try:
            msg = str((e.body or {}).get("message") or "") if isinstance(e.body, dict) else ""
        except Exception:  # noqa: BLE001
            msg = ""
        msg = providers._redact(msg, key)[:240]
        raise ImageError("OpenAI refused the picture (HTTP %d)%s" % (
            e.status_code, ": " + msg if msg else ".")) from None
    except openai.OpenAIError as e:
        raise ImageError("Could not reach OpenAI (%s)." % type(e).__name__) from None
    data = (r.data or [None])[0]
    b64 = getattr(data, "b64_json", None)
    if not b64:
        raise ImageError("OpenAI answered without a picture.")
    usage = r.usage.model_dump() if getattr(r, "usage", None) is not None else {}
    try:
        return base64.b64decode(b64), usage
    except ValueError:
        raise ImageError("OpenAI's picture could not be decoded.") from None


# ------------------------------------------------------------------------------ cropping

def _bright_runs(values: list[float], lo: int, hi: int, thresh: float) -> tuple[int, int] | None:
    """The longest run of indexes in [lo, hi) whose brightness >= thresh."""
    best = None
    start = None
    for i in range(max(lo, 0), min(hi, len(values))):
        if values[i] >= thresh:
            start = i if start is None else start
            if best is None or i - start > best[1] - best[0]:
                best = (start, i)
        else:
            start = None
    return best


def _cuts(profile: list[float], parts: int, thresh: float = 236.0) -> list[tuple[int, int]]:
    """[start, end) of each of `parts` cells along one axis, at the white gutters nearest
    the expected boundaries (falls back to the expected place)."""
    n = len(profile)
    lo = 0
    while lo < n * 0.06 and profile[lo] >= thresh:
        lo += 1
    hi = n
    while hi > n * 0.94 and profile[hi - 1] >= thresh:
        hi -= 1
    if hi - lo < n * 0.5:
        lo, hi = 0, n
    cell = (hi - lo) / parts
    edges = [(lo, None)]
    for k in range(1, parts):
        x = lo + cell * k
        win = int(cell * 0.12)
        run = _bright_runs(profile, int(x) - win, int(x) + win, thresh)
        edges.append((run[0], run[1] + 1) if run else (int(x), int(x)))
    edges.append((None, hi))
    out = []
    for k in range(parts):
        start = edges[k][1] if k else lo
        end = edges[k + 1][0] if k + 1 < parts else hi
        out.append((start, end))
    return out


def split_grid(img, rows: int, cols: int, count: int) -> list:
    """The first `count` panels of a rows x cols grid picture, row by row."""
    from PIL import Image
    gray = img.convert("L")
    colp = list(gray.resize((gray.width, 1), Image.Resampling.BOX).tobytes())
    rowp = list(gray.resize((1, gray.height), Image.Resampling.BOX).tobytes())
    xs, ys = _cuts(colp, cols), _cuts(rowp, rows)
    out = []
    for r in range(rows):
        for c in range(cols):
            x0, x1 = xs[c]
            y0, y1 = ys[r]
            inset = 3
            out.append(img.crop((x0 + inset, y0 + inset, max(x0 + inset + 8, x1 - inset),
                                 max(y0 + inset + 8, y1 - inset))))
    return out[:count]


# ------------------------------------------------------------------------------ the banner

def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    names = (("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "arialbd.ttf") if bold else
             ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"))
    for f in names:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


class _Canvas:
    """ImageDraw plus a record of every string drawn (the tests read it)."""

    def __init__(self, img):
        from PIL import ImageDraw
        self.img = img
        self.d = ImageDraw.Draw(img)
        self.texts: list[dict] = []

    def text(self, xy, s: str, font, fill, role: str):
        self.d.text(xy, s, font=font, fill=fill)
        self.texts.append({"role": role, "text": s})

    def width(self, s: str, font) -> float:
        return self.d.textlength(s, font=font)


def _wrap(cv: _Canvas, words: list[str], font, first_w: float, w: float) -> list[str]:
    lines, cur, limit = [], "", first_w
    for word in words:
        trial = (cur + " " + word).strip()
        if cur and cv.width(trial, font) > limit:
            lines.append(cur)
            cur, limit = word, w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _fit_title(cv: _Canvas, s: str, max_w: float):
    for size in (30, 28, 26, 24, 22):
        f = _font(size)
        if cv.width(s, f) <= max_w:
            return s, f
    f = _font(22)
    while s and cv.width(s + "…", f) > max_w:
        s = s[:-1]
    return s.rstrip() + "…", f


def _tile_image(src, w: int, h: int):
    from PIL import Image, ImageOps
    if src is None:
        return Image.new("RGB", (w, h), (236, 238, 242))
    return ImageOps.fit(src.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)


def render_banner(p: dict, step_images: list, final_image, everything_image=None):
    """One option's 1536x512 strip. Returns (PIL image, every string drawn with its role)."""
    from PIL import Image
    img = Image.new("RGB", (W, H), (255, 255, 255))
    cv = _Canvas(img)
    d = cv.d
    # Title bar
    d.rectangle((0, 0, W, 62), fill=NAVY)
    d.rectangle((0, 0, 11, 62), fill=ACCENT.get(p["name"], ACCENT["GOOD"]))
    title, tf = _fit_title(cv, p["title"], W - 60)
    cv.text((28, 31 - tf.size // 2 - 3), title, tf, (255, 255, 255), "title")
    # Tiles
    tiles = []
    if p.get("first"):
        tiles.append(("first", p["first"]["label"], p["first"]["caption"], everything_image))
    for i, s in enumerate(p["steps"]):
        tiles.append(("step", str(p["start"] + i), s["caption"],
                      step_images[i] if i < len(step_images) else None))
    tiles.append(("final", None, "Final result", final_image))
    k = len(tiles)
    gap = 8
    tw = int(min(360, (W - 16 - gap * (k - 1)) / k))
    x = (W - (tw * k + gap * (k - 1))) // 2
    top, th = 70, 282
    tag = p.get("tag")
    for kind, label, cap, src in tiles:
        border, bw = (LIGHT, 2)
        if kind == "first":
            border, bw = GREY, 4
        elif kind == "final":
            border, bw = GREEN, 4
        elif tag:
            border, bw = tag[1], 4
        img.paste(_tile_image(src, tw, th), (x, top))
        d.rectangle((x, top, x + tw - 1, top + th - 1), outline=border, width=bw)
        if kind == "step" and tag:
            f = _font(16)
            tw_ = cv.width(tag[0], f)
            d.rectangle((x + bw, top + bw, x + bw + tw_ + 16, top + bw + 26), fill=tag[1])
            cv.text((x + bw + 8, top + bw + 4), tag[0], f, (255, 255, 255), "tag")
        cy = top + th + 10
        if kind == "final":
            f = _font(19)
            d.rounded_rectangle((x, cy, x + tw - 1, cy + 34), radius=8, fill=GREEN)
            cv.text((x + (tw - cv.width(cap, f)) / 2, cy + 6), cap, f, (255, 255, 255),
                    "final")
        else:
            bf = _font(17)
            lw = cv.width(label, bf)
            bh = 28
            bwid = max(bh, lw + 14)
            d.rounded_rectangle((x + 2, cy, x + 2 + bwid, cy + bh), radius=bh // 2, fill=NAVY)
            cv.text((x + 2 + (bwid - lw) / 2, cy + 4), label, bf, (255, 255, 255), "number")
            words = cap.split()
            for size, most in ((20, 2), (18, 2), (18, 3), (16, 3), (14, 3)):
                cf = _font(size)
                lines = _wrap(cv, words, cf, tw - bwid - 12, tw - 4)
                if len(lines) <= most:
                    break
            for j, line in enumerate(lines[:3]):
                lx = x + bwid + 9 if j == 0 else x + 2
                cv.text((lx, cy + 3 + j * (cf.size + (9 if len(lines) < 3 else 6))), line, cf,
                        INK,
                        "caption" if kind == "step" else "everything")
        x += tw + gap
    # Bottom bar
    d.rectangle((0, 466, W, H), fill=NAVY)
    cv.text((20, 478), DISCLAIMER, _font(18, bold=False), (222, 226, 236), "disclaimer")
    if p.get("warranty"):
        f = _font(20)
        ww = cv.width(p["warranty"], f)
        x1 = W - 12
        x0 = x1 - max(288, ww + 36)
        d.rounded_rectangle((x0, 473, x1, 507), radius=6, fill=GREEN)
        cv.text((x0 + 18, 478), p["warranty"], f, (255, 255, 255), "warranty")
    return img, cv.texts


def banners_from_grids(plans: list[dict], grids: dict[str, bytes]) -> dict[str, tuple]:
    """name -> (jpeg bytes, texts). Each option's grid gives its steps and its final result;
    BETTER's / BEST's first tile is the previous option's final result."""
    from PIL import Image
    out, prev_final = {}, None
    for p in plans:
        src = Image.open(io.BytesIO(grids[p["name"]])).convert("RGB")
        n = len(p["steps"]) + 1
        rows, cols = grid(n)
        cells = split_grid(src, rows, cols, n)
        img, texts = render_banner(p, cells[:-1], cells[-1], prev_final)
        prev_final = cells[-1]
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88, optimize=True)
        out[p["name"]] = (buf.getvalue(), texts)
    return out


# ------------------------------------------------------------------------------ files

def row_dir(row_id: int) -> Path:
    return config.picture_dir() / str(int(row_id))


def file_path(row: OwenAiProposalDraft, name: str) -> Path | None:
    """A current banner of this row, or None (any other name, a stale one, a missing file)."""
    if not NAME_RX.match(name or ""):
        return None
    files = ((row.pictures or {}).get("files") or {}).values()
    if name not in files or row.pictures_status not in ("ready", "attaching", "attached"):
        return None
    base = row_dir(row.id).resolve()
    p = (base / name).resolve()
    if p.parent != base or not p.is_file():
        return None
    return p


def _clear(row_id: int, keep: set[str] = frozenset()) -> None:
    d = row_dir(row_id)
    if not d.is_dir():
        return
    for f in d.iterdir():
        if f.name not in keep and f.is_file():
            f.unlink(missing_ok=True)


def url(row_id: int, name: str) -> str:
    return "/api/owen-ai/proposals/%d/pictures/%s" % (row_id, name)


# ------------------------------------------------------------------------------ generate

def _get(db: Session, row_id: int) -> OwenAiProposalDraft:
    row = db.get(OwenAiProposalDraft, row_id)
    if row is None:
        raise LookupError("no such draft")
    return row


def _created(row: OwenAiProposalDraft) -> None:
    if row.status != "created" or not row.estimate_uid:
        raise Refused("Step pictures are only made for a proposal that is approved and created in "
                      "Zuper; this draft is %s." % row.status)


def generate(db: Session, row_id: int, who: str) -> OwenAiProposalDraft:
    """Make the banners for preview. Talks to OpenAI only — never to Zuper."""
    row = _get(db, row_id)
    _created(row)
    why = refusal(db)
    if why:
        raise Refused(why)
    if row.pictures_status == "attached":
        raise Refused("The step pictures are already on %s." % (row.estimate_number or "it"))
    plans = plan(row)
    key = openai_key(db)
    if not key:
        raise Refused(NO_KEY)
    stale = _now() - STALE
    took = db.execute(update(OwenAiProposalDraft).where(
        OwenAiProposalDraft.id == row.id, OwenAiProposalDraft.status == "created",
        or_(OwenAiProposalDraft.pictures_status.is_(None),
            OwenAiProposalDraft.pictures_status.in_(("ready", "failed", "discarded")),
            (OwenAiProposalDraft.pictures_status == "generating")
            & (OwenAiProposalDraft.pictures_at < stale)))
        .values(pictures_status="generating", pictures_at=_now(), pictures_error=None)
        .execution_options(synchronize_session=False)).rowcount
    db.commit()
    db.refresh(row)
    if not took:
        raise Refused("Step pictures for this proposal are already %s." % (
            "being made" if row.pictures_status == "generating" else row.pictures_status))
    calls: list[dict] = []
    grids: dict[str, bytes] = {}
    errors: list[str] = []

    def one(p: dict):
        return p["name"], generate_image(key, prompt(p))

    with ThreadPoolExecutor(max_workers=len(plans)) as pool:
        futures = [pool.submit(one, p) for p in plans]
        for p, fut in zip(plans, futures, strict=True):
            try:
                name, (png, usage) = fut.result()
            except ImageError as exc:
                errors.append("%s: %s" % (p["name"], exc))
                continue
            except Exception as exc:  # noqa: BLE001 - a bug still ends as a sentence
                errors.append("%s: internal error (%s)" % (p["name"], type(exc).__name__))
                continue
            grids[name] = png
            c = cost_usd(usage)
            calls.append({"option": name, "model": MODEL, "usage": usage, "cost_usd": c})
            log.info("owen-ai step pictures: draft %d %s: %s tokens in, %s out, $%.4f",
                     row.id, name, usage.get("input_tokens"), usage.get("output_tokens"), c)
    spent = round(sum(c["cost_usd"] for c in calls), 4)
    base_info = {"calls": calls, "model": MODEL, "by": who, "plan": plans}
    if errors:
        row.pictures_status, row.pictures_cost_usd = "failed", spent
        row.pictures_error = ("No pictures were made — %s%s Nothing was put on the proposal." % (
            "; ".join(errors), "" if errors[-1].endswith(".") else "."))
        row.pictures = base_info
        _clear(row.id)
        db.commit()
        db.refresh(row)
        return row
    try:
        made = banners_from_grids(plans, grids)
        d = row_dir(row.id)
        d.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, (jpg, _texts) in made.items():
            fname = "%s-%s.jpg" % (name.lower(), secrets.token_hex(4))
            (d / fname).write_bytes(jpg)
            files[name] = fname
        _clear(row.id, keep=set(files.values()))
    except Exception as exc:
        log.exception("owen-ai step pictures: draft %d: drawing failed", row.id)
        _clear(row.id)
        row.pictures_status, row.pictures_cost_usd = "failed", spent
        row.pictures_error = ("The pictures came back but the banners could not be drawn (%s). "
                              "Nothing was put on the proposal." % type(exc).__name__)
        row.pictures = base_info
        db.commit()
        db.refresh(row)
        return row
    row.pictures = {**base_info, "files": files, "made_at": _now().isoformat()}
    row.pictures_status, row.pictures_cost_usd, row.pictures_error = "ready", spent, None
    row.pictures_at = _now()
    db.commit()
    db.refresh(row)
    return row


# ------------------------------------------------------------------------------ attach

def _upload_url(payload: Any) -> str:
    data = zc.data_of(payload)
    if isinstance(data, dict):
        for k in ("attachment_path", "url", "file_url"):
            v = data.get(k)
            if isinstance(v, str) and v.startswith("https://"):
                return v
    raise zc.ZuperError("bad_response", "the upload answered without an attachment_path (%s)"
                        % zc.shape(payload))


def _total(o: dict) -> float | None:
    try:
        return float(o.get("total"))
    except (TypeError, ValueError):
        return None


def attach(db: Session, row_id: int, who: str) -> OwenAiProposalDraft:
    """Put the previewed banners on the proposal: uploads + ONE PUT + read-back. Never retried."""
    row = _get(db, row_id)
    _created(row)
    why = refusal(db)
    if why:
        raise Refused(why)
    if row.pictures_status != "ready":
        raise Refused("There are no step pictures waiting to be put on this proposal (%s)." % (
            row.pictures_status or "none made yet"))
    files = (row.pictures or {}).get("files") or {}
    paths = {n: file_path(row, f) for n, f in files.items()}
    if not files or any(p is None for p in paths.values()):
        raise Refused("The previewed pictures are missing on the server; make them again.")
    took = db.execute(update(OwenAiProposalDraft).where(
        OwenAiProposalDraft.id == row.id, OwenAiProposalDraft.pictures_status == "ready")
        .values(pictures_status="attaching", pictures_error=None)
        .execution_options(synchronize_session=False)).rowcount
    db.commit()
    db.refresh(row)
    if not took:
        raise Refused("These pictures are already %s." % row.pictures_status)
    uid = row.estimate_uid
    label = row.estimate_number or uid
    put_sent = False
    try:
        with zc.owen_ai_proposal_pictures(uid):
            cur = zc.data_of(zc.request("GET", "/estimate/" + uid))
            cur = cur if isinstance(cur, dict) else {}
            opts = [o for o in cur.get("proposal_options") or [] if isinstance(o, dict)]
            names = {str(o.get("option_name") or "").upper() for o in opts}
            if str(cur.get("estimate_status") or "").upper() != "DRAFT" or cur.get("is_deleted") \
                    or cur.get("is_proposal") is False:
                raise LookupError("%s is no longer a DRAFT proposal (status %s); nothing was "
                                  "changed." % (label, cur.get("estimate_status")))
            job = cur.get("job")
            job_uid = job.get("job_uid") if isinstance(job, dict) else job
            if job_uid and job_uid != row.job_uid:
                raise LookupError("%s is not on job #%s any more; nothing was changed." % (
                    label, row.job_number or row.job_uid))
            if names != set(files):
                raise LookupError("%s has the options %s now, the pictures are for %s; nothing "
                                  "was changed." % (label, ", ".join(sorted(names)) or "none",
                                                    ", ".join(sorted(files))))
            urls = {}
            for o in opts:
                n = str(o["option_name"]).upper()
                data = paths[n].read_bytes()
                fname = "%s-%s-steps.jpg" % (label, n.lower())
                urls[n] = _upload_url(zc.request(
                    "POST", "/misc/upload", files={"attachment": (fname, data, "image/jpeg")},
                    form={"attachment_type": "ATTACHMENT"}))
            new = copy.deepcopy(cur["proposal_options"])
            for o in new:
                if isinstance(o, dict):
                    o["option_image"] = urls[str(o["option_name"]).upper()]
            diff = zc.only_option_images_changed(cur["proposal_options"], new)
            if diff:
                raise LookupError("refused to send a change other than the pictures: %s" %
                                  "; ".join(diff))
            body = {"estimate": {"estimate_uid": uid, "is_proposal": True,
                                 "proposal_title": cur.get("proposal_title"),
                                 "proposal_options": new}}
            put_sent = True
            zc.request("PUT", "/estimate/" + uid, body=body)
            back = zc.data_of(zc.request("GET", "/estimate/" + uid))
            back = back if isinstance(back, dict) else {}
        problems = []
        if str(back.get("estimate_status") or "").upper() != "DRAFT":
            problems.append("status is %s, not DRAFT" % back.get("estimate_status"))
        got = {str(o.get("option_name") or "").upper(): o
               for o in back.get("proposal_options") or [] if isinstance(o, dict)}
        for o in opts:
            n = str(o["option_name"]).upper()
            g = got.get(n)
            if g is None:
                problems.append("%s is missing" % n)
                continue
            if not g.get("option_image"):
                problems.append("%s has no picture" % n)
            if _total(g) is None or _total(o) is None or abs(_total(g) - _total(o)) > 0.005:
                problems.append("%s total is %s, was %s" % (n, g.get("total"), o.get("total")))
        row.pictures = {**(row.pictures or {}), "uploaded": urls, "attached_by": who,
                        "attached_at": _now().isoformat()}
        if problems:
            row.pictures_status = "failed"
            row.pictures_error = ("The pictures were sent to %s but the read-back does not match: "
                                  "%s. Check it in Zuper before doing anything else." % (
                                      label, "; ".join(problems)))
        else:
            row.pictures_status, row.pictures_error = "attached", None
    except zc.ZuperError as exc:
        row.pictures_status = "ready"
        row.pictures_error = "Nothing was put on %s: %s%s" % (
            label, zc.sentence(exc),
            " (Zuper may have taken the change — check before trying again.)" if put_sent
            else ". You can try again.")
    except LookupError as exc:
        row.pictures_status, row.pictures_error = "ready", str(exc)
    except Exception as exc:
        log.exception("owen-ai step pictures: draft %d: attach failed", row.id)
        row.pictures_status = "ready"
        row.pictures_error = "Internal error (%s)%s." % (
            type(exc).__name__, "; Zuper may have taken the change" if put_sent else
            "; nothing was put on the proposal")
    if row.pictures_status == "attached":
        proposals._note(db, row, "Step pictures were put on %s by %s (a DRAFT; nothing was sent "
                                 "to the customer)." % (label, who))
    db.commit()
    db.refresh(row)
    return row


def discard(db: Session, row_id: int, who: str) -> OwenAiProposalDraft:
    """Throw the previewed pictures away. Writes nothing to Zuper."""
    row = _get(db, row_id)
    took = db.execute(update(OwenAiProposalDraft).where(
        OwenAiProposalDraft.id == row.id,
        OwenAiProposalDraft.pictures_status.in_(("ready", "failed")))
        .values(pictures_status="discarded", pictures_error=None)
        .execution_options(synchronize_session=False)).rowcount
    db.commit()
    db.refresh(row)
    if not took:
        raise Refused("There are no step pictures to discard (%s)." % (
            row.pictures_status or "none made yet"))
    _clear(row.id)
    shutil.rmtree(row_dir(row.id), ignore_errors=True)
    row.pictures = {**(row.pictures or {}), "files": {}, "discarded_by": who}
    db.commit()
    db.refresh(row)
    return row


# ------------------------------------------------------------------------------ the API's view

def out(row: OwenAiProposalDraft) -> dict:
    files = (row.pictures or {}).get("files") or {}
    shown = row.pictures_status in ("ready", "attaching", "attached")
    order = [o["name"] for o in sorted(row.options or [], key=lambda o:
                                       proposals.OPTION_NAMES.index(o["name"]))]
    return {"pictures_status": row.pictures_status, "pictures_error": row.pictures_error,
            "pictures_cost_usd": row.pictures_cost_usd,
            "pictures_at": _aware(row.pictures_at).isoformat() if row.pictures_at else None,
            "pictures_estimate_usd": estimate_usd(row),
            "pictures": [{"name": n, "url": url(row.id, files[n])} for n in order
                         if shown and n in files]}


# ------------------------------------------------------------------------------ the chat tool

def find_row(db: Session, ref: Any) -> OwenAiProposalDraft | None:
    """A draft id (digits, or "#12") or a DTR number ("DTR-160")."""
    s = str(ref or "").strip()
    m = re.fullmatch(r"(?i)dtr[\s-]*(\d+)", s)
    if m:
        return db.scalars(select(OwenAiProposalDraft).where(
            OwenAiProposalDraft.estimate_number == "DTR-" + m.group(1))
            .order_by(OwenAiProposalDraft.id.desc())).first()
    m = re.fullmatch(r"#?(\d+)", s)
    return db.get(OwenAiProposalDraft, int(m.group(1))) if m else None


ACTIONS = ("generate", "attach", "discard")
WHO = "Owen (asked Owen AI)"


def tool_pictures(args: dict) -> str:
    args = args if isinstance(args, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return "ERROR: action must be one of %s" % ", ".join(ACTIONS)
    db = proposals._session()
    try:
        row = find_row(db, args.get("proposal"))
        if row is None:
            return ("ERROR: no proposal draft %s. Pass the draft id (from the Proposals tab) or "
                    "the DTR number, like DTR-160." % args.get("proposal"))
        label = "%s (draft #%d, job #%s)" % (row.estimate_number or "the proposal", row.id,
                                             row.job_number or row.job_uid)
        try:
            if action == "generate":
                row = generate(db, row.id, WHO)
            elif action == "attach":
                row = attach(db, row.id, WHO)
            else:
                row = discard(db, row.id, WHO)
        except Refused as exc:
            return "ERROR: %s" % exc
        if action == "discard":
            return "The step pictures for %s were discarded. Nothing was changed in Zuper." % label
        if action == "attach":
            if row.pictures_status == "attached":
                return ("The step pictures are now on %s — still a DRAFT in Zuper; nothing was "
                        "sent to the customer." % label)
            return "ERROR: %s" % (row.pictures_error or "the pictures were not attached")
        if row.pictures_status != "ready":
            return "ERROR: %s Cost so far: $%.2f." % (
                row.pictures_error or "No pictures were made.", row.pictures_cost_usd or 0)
        files = (row.pictures or {}).get("files") or {}
        lines = ["Step pictures for %s are ready for Owen to look at (OpenAI cost $%.2f). They are "
                 "NOT on the proposal yet:" % (label, row.pictures_cost_usd or 0)]
        for p in out(row)["pictures"]:
            lines.append("- [%s steps](%s)" % (p["name"], p["url"]))
        lines.append("Show Owen these links as they are. Attach only when he says so (action "
                     "attach); discard if he does not want them. %d banner%s." % (
                         len(files), "" if len(files) == 1 else "s"))
        return "\n".join(lines)
    finally:
        db.close()


DESCRIPTION = (
    "OPTIONAL step-by-step pictures for a proposal that is already APPROVED and CREATED in Zuper "
    "(a DRAFT). Only when Owen asks. `proposal` = the draft id or the DTR number (DTR-160). "
    "action generate: makes one banner per option from the proposal's own scope (OpenAI, about "
    "$0.17 per option, ~$0.51 for Good/Better/Best) and returns preview links to show Owen — "
    "nothing reaches Zuper. action attach: puts the previewed banners on the proposal "
    "(option_image only; everything else unchanged, read back; still a DRAFT, never sent) — "
    "only when Owen said to. action discard: throws the previews away. Refused on a proposal "
    "that is not created, while the server switches are off, or while pictures are being made.")

SPECS = (("proposal_step_pictures", DESCRIPTION,
          {"type": "object", "properties": {
              "proposal": {"type": "string", "description": "draft id or DTR-N"},
              "action": {"type": "string", "enum": list(ACTIONS)}},
           "required": ["proposal", "action"]},
          tool_pictures),)
