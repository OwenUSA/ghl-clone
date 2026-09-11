"""Opportunity custom fields — the definitions, and what an answer may be.

`CustomFieldDef` says a field exists. `Opportunity.custom_fields` holds the answers.
This module is the only place that knows how those two fit together, so the API, the
create path and the detail PATCH cannot drift on what an answer is allowed to be.

Three rules do all the safety work here, and every one of them exists because the
values in that blob are things a customer told somebody on the phone:

1. **An answer is never destroyed by an edit it was not part of.** `merge_answers`
   MERGES: a key the client did not send keeps the value it had. Clearing an answer
   is an explicit act — you send the key with an empty value — not something that
   happens because a form rendered a shorter list of fields than the last one did.

2. **Hidden is not gone.** A field attached to pipelines A and B is not shown on a
   deal in pipeline C, and neither is an archived field. Their answers stay in the
   blob untouched and reappear the moment the deal moves back or the field is
   un-archived. They also cannot be *written* from a form that is not showing them,
   which is refused out loud rather than ignored.

3. **The `owen_` namespace belongs to the telephony project.** No definition may
   claim a key in it, and no write through this module can drop one of those keys.
   `owen_call_id` keeps its existing guard: it is the join key, and changing it
   breaks call attribution in a separate production system (DECISIONS.md).
"""
import math
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import CustomFieldDef, CustomFieldPipeline

# Namespaces this app does not own. A key under one of these is written by a
# machine, read by a machine, and shown to a person — never edited by one.
#
#   owen_*    the telephony project ("from OWEN"). See DECISIONS.md — the four keys
#             were measured on the live GHL account before any of this was built.
#   workiz_*  the Workiz migration (`app/workiz_import.py`, 2026-09-11).
#             `workiz_id` is the Client # on a contact and the Job # on an
#             opportunity, and it is the whole of the import's idempotency: the
#             importer finds a record by it and UPDATES rather than duplicating.
#             Editing one by hand would silently make the next import create a
#             second copy of that customer, so the API refuses. A record created in
#             the CRM by hand simply has no workiz_id, which is expected.
#
# One tuple, checked in one function, so a third namespace is a one-line change and
# not a second guard that drifts from this one.
RESERVED_PREFIXES = ("owen_", "workiz_")
# Kept as a name because it reads better in the message `claim_key` raises, and
# because it is the prefix that has been in this codebase the longest.
RESERVED_PREFIX = RESERVED_PREFIXES[0]
# The one key that is a live join key into another production system's data.
JOIN_KEY = "owen_call_id"
# The import's identity key. Unlike `owen_call_id`, which may be SET where there was
# none, this one is immutable in every direction through this module — see
# `_check_reserved`.
IMPORT_PREFIX = "workiz_"

KEY_MAX = 64
LABEL_MAX = 160
TEXT_MAX = 2000
OPTION_MAX = 80
OPTIONS_MAX = 50

TEXT = CustomFieldDef.TEXT
NUMBER = CustomFieldDef.NUMBER
DROPDOWN = CustomFieldDef.DROPDOWN
DATE = CustomFieldDef.DATE
BOOLEAN = CustomFieldDef.BOOLEAN
TYPES = CustomFieldDef.TYPES

# Accepted spellings for a yes/no answer. The browser sends "true"/"false"; the CLI
# and a human typing JSON send all of these, and refusing "yes" for a field labelled
# "yes/no" would be a joke at the user's expense.
_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def is_reserved(key: str) -> bool:
    return key.startswith(RESERVED_PREFIXES)


def slug_for(label: str) -> str:
    """The storage key for a label, derived ONCE at creation and then immutable.

    The key is what every recorded answer is filed under, so a later rename of the
    label must not touch it — otherwise editing "How old is the roof?" into "Roof
    age" would orphan every answer already given. ASCII only: the key ends up in a
    JSON object, a URL and a CSV header, and a smart-quote in it helps nobody.
    """
    flat = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in label.lower())
    return "_".join(part for part in flat.split("_") if part)[:KEY_MAX].strip("_")


# ---------- reading definitions ----------

def load_defs(db: Session, *, include_archived: bool = True) -> list[CustomFieldDef]:
    stmt = select(CustomFieldDef).options(selectinload(CustomFieldDef.pipelines))
    if not include_archived:
        stmt = stmt.where(CustomFieldDef.archived_at.is_(None))
    # `id` is the tiebreak for the same reason the board uses one: `position`
    # defaults to 0, so two fields can share it and the order would otherwise
    # change between two identical reads.
    return list(db.scalars(
        stmt.order_by(CustomFieldDef.position, CustomFieldDef.id)).all())


def pipeline_ids(d: CustomFieldDef) -> list[int]:
    return sorted(link.pipeline_id for link in d.pipelines)


def shows_on(d: CustomFieldDef, pipeline_id: int | None) -> bool:
    """Is this field asked on a deal in `pipeline_id`?

    Attachment is explicit. A field attached to no pipeline shows on no deal —
    an empty set never means "everywhere", so detaching the last pipeline cannot
    silently turn "AHS claim number" into a question on every job.
    """
    if d.archived_at is not None or pipeline_id is None:
        return False
    return any(link.pipeline_id == pipeline_id for link in d.pipelines)


def describe(d: CustomFieldDef) -> dict:
    return {
        "id": d.id,
        "key": d.key,
        "label": d.label,
        "field_type": d.field_type,
        "options": list(d.options or []),
        "position": d.position,
        "entity": d.entity,
        "archived": d.archived_at is not None,
        "archived_at": d.archived_at,
        "pipeline_ids": pipeline_ids(d),
    }


# ---------- defining a field ----------

def clean_label(label: str | None) -> str:
    cleaned = (label or "").strip()
    if not cleaned:
        raise HTTPException(400, "a custom field needs a question to ask")
    if len(cleaned) > LABEL_MAX:
        raise HTTPException(400, "a custom field's question is limited to %d characters"
                            % LABEL_MAX)
    return cleaned


def clean_type(field_type: str | None) -> str:
    if field_type not in TYPES:
        raise HTTPException(400, "%r is not a field type — expected one of %s" % (
            field_type, ", ".join(TYPES)))
    return field_type


def clean_options(field_type: str, options: list | None) -> list[str]:
    """Only a dropdown has options, and a dropdown without them offers nothing."""
    values = [str(o).strip() for o in (options or []) if str(o).strip()]
    if field_type != DROPDOWN:
        return []
    if not values:
        raise HTTPException(400, "a dropdown needs at least one option to choose from")
    if len(values) > OPTIONS_MAX:
        raise HTTPException(400, "a dropdown is limited to %d options" % OPTIONS_MAX)
    for v in values:
        if len(v) > OPTION_MAX:
            raise HTTPException(400, "the option %r is longer than %d characters"
                                % (v, OPTION_MAX))
    seen, unique = set(), []
    for v in values:
        if v.lower() in seen:
            continue
        seen.add(v.lower())
        unique.append(v)
    return unique


def claim_key(db: Session, label: str) -> str:
    """Derive the storage key, and refuse the two ways it can be wrong.

    The reserved refusal is the safety requirement. A field defined as
    `owen_campaign` would put a form control on top of live call-attribution data;
    a field defined as `workiz_id` would put one on top of the key the importer
    matches records by, and the next import would then create a second copy of
    every customer somebody had edited. Note the check is on the DERIVED key, so
    labelling a field "Owen campaign" or "Workiz ID" is refused too — that is the
    case a prefix check on the label alone would have let through.
    """
    key = slug_for(label)
    if not key:
        raise HTTPException(400, "that question has no letters or digits in it, so "
                                 "there is nothing to store the answers under")
    if is_reserved(key):
        owner = ("the telephony project — those fields are written by OWEN"
                 if key.startswith(RESERVED_PREFIX)
                 else "the Workiz migration — those fields are written by the "
                      "importer")
        raise HTTPException(400, (
            "%r starts with %r, which belongs to %s and cannot be redefined here. "
            "Try a different wording." % (
                key, next(p for p in RESERVED_PREFIXES if key.startswith(p)),
                owner)))
    clash = db.scalar(select(CustomFieldDef).where(CustomFieldDef.key == key))
    if clash:
        raise HTTPException(409, (
            "%r is already the key for %r%s. Two fields cannot share a key — the "
            "answers would land on top of each other." % (
                key, clash.label, " (archived)" if clash.archived_at else "")))
    return key


# ---------- answers ----------

def _shown(value) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _reject(d: CustomFieldDef, value, expected: str) -> None:
    raise HTTPException(400, "“%s” takes %s, and “%s” is not one." % (
        d.label, expected, _shown(value)))


def coerce_answer(d: CustomFieldDef, raw):
    """The stored form of one answer, or None for "not answered".

    Validation is by TYPE, at the API, so the CLI and the telephony feed get the
    same answer as the browser. An empty string is not a failed number — it is a
    question nobody has answered yet, and it is stored by removing the key.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None

    if d.field_type == NUMBER:
        # `bool` is an `int` in Python, so True would otherwise store as 1.
        if isinstance(raw, (bool, list, dict)):
            _reject(d, raw, "a number")
        if isinstance(raw, (int, float)):
            number = float(raw)
        else:
            try:
                number = float(str(raw).strip())
            except ValueError:
                _reject(d, raw, "a number")
        if not math.isfinite(number):
            _reject(d, raw, "a number")
        # 3.0 came in as "3"; store it as 3 so the CSV and the form agree.
        return int(number) if number.is_integer() else number

    if d.field_type == DROPDOWN:
        value = str(raw).strip()
        options = list(d.options or [])
        if value not in options:
            raise HTTPException(400, "“%s” does not offer “%s”. The choices are: %s."
                                % (d.label, _shown(value), ", ".join(options) or "none"))
        return value

    if d.field_type == DATE:
        value = str(raw).strip()
        try:
            date.fromisoformat(value)
        except ValueError:
            _reject(d, raw, "a date like 2026-09-11")
        return value

    if d.field_type == BOOLEAN:
        if isinstance(raw, bool):
            return raw
        word = str(raw).strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        _reject(d, raw, "yes or no")

    # TEXT
    if isinstance(raw, (list, dict)):
        _reject(d, raw, "text")
    value = str(raw).strip()
    if len(value) > TEXT_MAX:
        raise HTTPException(400, "“%s” is limited to %d characters." % (d.label, TEXT_MAX))
    return value


def merge_answers(db: Session, *, pipeline_id: int | None,
                  existing: dict | None, incoming: dict | None) -> dict:
    """The blob an opportunity should hold after this write.

    MERGE, not replace. What that buys, in order of how badly it would hurt:

    * A reserved `owen_*` key can never be dropped, whatever the client sends.
      Deleting `owen_campaign` would silently break call attribution, and until now
      any client that posted a shorter object did exactly that.
    * An answer to a field that is not shown on THIS deal — wrong pipeline, or
      archived — is kept exactly as it stands. That is what makes "move it to
      another pipeline and back and the answer is still there" true.
    * An answer the client simply did not mention is left alone, rather than read
      as a deletion.

    Clearing is explicit: send the key with "" or null.
    """
    existing = dict(existing or {})
    incoming = dict(incoming or {})
    defs = {d.key: d for d in load_defs(db)}
    result: dict = {}

    # --- the reserved namespace ---
    was = existing.get(JOIN_KEY)
    now = incoming.get(JOIN_KEY, was)
    if was is not None and now != was:
        raise HTTPException(
            400, "owen_call_id is the telephony join key and cannot be changed here")
    # `workiz_*` is stricter than `owen_call_id`, which may still be SET where there
    # was none. These are read-only in every direction: not set, not changed, not
    # removed. The importer writes them through the ORM and nothing else may. An
    # unchanged echo is not a write — the detail form posts the whole blob back on
    # every save — so only a real difference is refused, and it is refused out loud
    # rather than ignored, because silently discarding a value somebody typed is how
    # an edit looks like it worked when it did not.
    for key in sorted(set(existing) | set(incoming)):
        if not key.startswith(IMPORT_PREFIX):
            continue
        if key in incoming and incoming[key] != existing.get(key):
            raise HTTPException(400, (
                "%s is written by the Workiz import and is read-only here — it is "
                "how a re-import recognises this record instead of creating a "
                "second copy of it." % key))
    # Kept first, and unconditionally: no path through this function drops one.
    for key, value in existing.items():
        if is_reserved(key):
            result[key] = value
    for key, value in incoming.items():
        if is_reserved(key):
            result[key] = value

    # --- defined fields ---
    for key, d in defs.items():
        if shows_on(d, pipeline_id):
            if key not in incoming:
                if key in existing:
                    result[key] = existing[key]
            elif incoming[key] == existing.get(key):
                # An unchanged echo is not a write and is never re-validated. It
                # matters: an admin who removes a dropdown option would otherwise
                # make every deal already holding that answer unsaveable, because
                # the form posts the whole object back on every save.
                result[key] = existing[key]
            else:
                answer = coerce_answer(d, incoming[key])
                if answer is not None:
                    result[key] = answer
            continue
        # Not shown here. The answer survives untouched...
        if key in existing:
            result[key] = existing[key]
        # ...and cannot be written from a form that is not showing the field.
        # Refused out loud: silently ignoring a value somebody typed is how an
        # answer goes missing without anyone noticing. An echo of what is already
        # stored is not a write, and neither is a blank where nothing is stored —
        # a client that posts the whole blob back must not be refused for it.
        if key in incoming and incoming[key] != existing.get(key) and not (
                key not in existing and (incoming[key] is None or (
                    isinstance(incoming[key], str) and not incoming[key].strip()))):
            raise HTTPException(400, (
                "“%s” is %s, so its answer cannot be set here. The answer already "
                "recorded is kept." % (
                    d.label,
                    "archived" if d.archived_at is not None
                    else "not asked on this pipeline")))

    # --- everything else ---
    # The column was free-form JSON before any of this existed and stays that way:
    # a key no definition claims is stored verbatim. `null` removes one, which is
    # the only way an undefined key can be taken out.
    for key, value in incoming.items():
        if key in defs or is_reserved(key):
            continue
        if value is None:
            continue
        result[key] = value
    for key, value in existing.items():
        if key in defs or is_reserved(key) or key in incoming:
            continue
        result[key] = value

    return result


def attach(db: Session, d: CustomFieldDef, ids: list[int] | None) -> None:
    """Replace the field's pipeline attachments. Caller validates the ids exist."""
    wanted = set(ids or [])
    for link in list(d.pipelines):
        if link.pipeline_id not in wanted:
            db.delete(link)
        else:
            wanted.discard(link.pipeline_id)
    for pipeline_id in sorted(wanted):
        db.add(CustomFieldPipeline(field_id=d.id, pipeline_id=pipeline_id))
    db.flush()
