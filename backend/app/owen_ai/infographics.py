"""The after-inspection report's option pictures (2026-11-12, the approved job #292 draft).

One picture per option (GOOD / BETTER / BEST): ONE OpenAI image edit (gpt-image-2, high,
1536x1024) given `assets/infographic-layout.jpg` — the layout the owner approved, from another
job, no customer data — as the style reference, and a prompt built ONLY from the option's
PictureSpec (steps, callouts, layers, top view), its name, headline and warranty. No customer
name, address, job number or price ever reaches OpenAI. The words are drawn by the model, so
Owen AI reads every new picture back against the spec (the skill), and Owen sees it on the page
before anything leaves the CRM — nothing here sends it anywhere.

A picture is named by the hash of everything it shows (`picture_hash`), so it is made once and
reused until that option's picture text changes. Off unless OWEN_AI_REPORT_PICTURES_ENABLED;
the key is the step pictures' (OWEN_AI_OPENAI_API_KEY, else an OpenAI AI connection). About
$0.18 a picture; each call's cost is returned to be kept on the report version.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from . import config, pictures
from .report import ASSETS, OptionBlock

LAYOUT = ASSETS / "infographic-layout.jpg"
LAYOUT_VERSION = 1                       # bump when LAYOUT or PROMPT changes: every hash moves
TIMEOUT_SECONDS = 300.0
SIZE = "1536x1024"
EST_COST = 0.18


class Refused(Exception):
    """Not made: the switch is off or there is no key (a sentence for Owen / Claude)."""


def refusal(db: Session) -> str | None:
    if not config.report_pictures_enabled():
        return config.REPORT_PICTURES_OFF
    if not pictures.openai_key(db):
        return pictures.NO_KEY
    return None


def picture_hash(o: OptionBlock, warranty: str) -> str:
    what = {"v": LAYOUT_VERSION, "name": o.name, "headline": o.headline, "warranty": warranty,
            "picture": o.picture.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(what, sort_keys=True).encode()).hexdigest()[:20]


PROMPT = """Create a new roofing proposal infographic with EXACTLY the same layout, style, fonts,
colours and level of detail as the reference image: a navy title bar; white panels with navy
headers; a left "Work Sequence" panel of numbered rows, each with a small photo-realistic
thumbnail, a bold title and one line of text; a large "Completed Repair - Overview" with white
callout labels and arrows; a cut-away "Section Detail" with labelled layers; a "Top View" with a
dashed repair-area box and a label; a green warranty badge bottom right.
{roof}{notes}Spell every word EXACTLY as written below and add no other text, no numbers other
than the step numbers, and no prices.

Title bar: "{title}"

Left panel header: "Work Sequence" - {n} rows:
{steps}

Top right panel header: "Completed Repair - Overview". Callouts: {callouts}.

Bottom middle panel header: "Section Detail - {section}". Layers labelled top to bottom: {layers}.

Bottom right panel header: "Top View - {top_title}". Label: "{top_label}".
{warranty}"""


def prompt(o: OptionBlock, warranty: str) -> str:
    p = o.picture
    roof = ("The roof: %s. " % p.roof) if p.roof else ""
    notes = ("%s. " % p.notes.rstrip(".")) if p.notes else ""
    return PROMPT.format(
        roof=roof, notes=notes, title="%s - %s" % (o.name, o.headline), n=len(p.steps),
        steps="\n".join('%d. "%s" / "%s"' % (i, s.title, s.text)
                        for i, s in enumerate(p.steps, 1)),
        callouts=", ".join('"%s"' % c for c in p.callouts), section=p.section_title,
        layers=", ".join('"%s"' % x for x in p.layers), top_title=p.top_title,
        top_label=p.top_label,
        warranty=('Green badge: "Warranty: %s".' % warranty) if warranty else
        "No warranty badge.")


def generate(db: Session, o: OptionBlock, warranty: str) -> tuple[bytes, float]:
    """One picture: (png bytes, cost in USD). Raises Refused or pictures.ImageError. No retry:
    every call is money."""
    why = refusal(db)
    if why:
        raise Refused(why)
    import openai

    from ..ai import providers
    key = pictures.openai_key(db)
    http = {"http_client": providers.HTTP_CLIENT_FACTORY()} if providers.HTTP_CLIENT_FACTORY \
        else {}
    client = openai.OpenAI(api_key=key, base_url=providers.OPENAI_BASE_URL,
                           timeout=TIMEOUT_SECONDS, max_retries=0, **http)
    try:
        with LAYOUT.open("rb") as ref:
            r = client.images.edit(model=pictures.MODEL, image=ref, prompt=prompt(o, warranty),
                                   n=1, size=SIZE, quality=pictures.QUALITY,
                                   output_format="png")
    except openai.APITimeoutError:
        raise pictures.ImageError("OpenAI did not answer within %d seconds."
                                  % TIMEOUT_SECONDS) from None
    except openai.APIStatusError as e:
        msg = ""
        if isinstance(e.body, dict):
            msg = providers._redact(str(e.body.get("message") or ""), key)[:240]
        raise pictures.ImageError("OpenAI refused the picture (HTTP %d)%s" % (
            e.status_code, ": " + msg if msg else ".")) from None
    except openai.OpenAIError as e:
        raise pictures.ImageError("Could not reach OpenAI (%s)." % type(e).__name__) from None
    b64 = getattr((r.data or [None])[0], "b64_json", None)
    if not b64:
        raise pictures.ImageError("OpenAI answered without a picture.")
    usage = r.usage.model_dump() if getattr(r, "usage", None) is not None else {}
    try:
        return base64.b64decode(b64), pictures.cost_usd(usage)
    except ValueError:
        raise pictures.ImageError("OpenAI's picture could not be decoded.") from None


def save(folder: Path, h: str, png: bytes) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    name = "%s.png" % h
    (folder / name).write_bytes(png)
    preview(folder / name)
    return name


PREVIEW_SIDE = 1280
PREVIEW_QUALITY = 82


def preview(path: Path) -> Path:
    """A small JPEG copy (~150-250 KB) of a picture, for Owen AI's Read tool: the full 1536 px
    PNG is ~2.5 MB, and a Read result that big ended Owen AI's turn (2026-11-12, the SDK's 1 MB
    message limit). Made once, beside the picture: '<name>.preview.jpg'. The words stay
    readable at this size."""
    out = path.with_name(path.stem + ".preview.jpg")
    if not out.is_file():
        from PIL import Image
        with Image.open(path) as src:
            im = src.convert("RGB")
        im.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
        im.save(out, "JPEG", quality=PREVIEW_QUALITY, optimize=True)
    return out
