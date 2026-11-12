"""Put a saved after-inspection report's PDF on its DRAFT proposal in Zuper (2026-11-12).

The user: "allow it to attach the after inspection into the proposal". Before this Owen AI could
only LINK a report to a proposal inside the CRM; the file had to be attached in Zuper by hand.

  * Only when a person asks: the "Attach to proposal" button on the Reports tab, or Owen AI's
    `attach_inspection_report` tool when Owen asks in the chat. Nothing is automatic.
  * Only a DRAFT proposal on the report's OWN job (the report's linked DTR number, or one named),
    and only a version that has a PDF — Zuper's Send pre-ticks every attachment, so this file
    reaches the customer whenever the proposal is sent. Missing header settings (phone, license,
    city) are a WARNING, not a refusal (the user, 2026-11-12): the page leaves them out.
  * Two writes, once each, checked by `client.owen_ai_report_attach` before a connection: upload
    the PDF, attach it (visible to the customer). Then a read-back: the file is on the proposal
    and it is still a DRAFT. Never retried, never sends. The same version already there -> nothing
    is written. An OLDER report PDF on it ('After-Inspection-Report-*', ours) is REPLACED (the
    user, 2026-11-12): removed after the new one is confirmed on it, so the customer never gets
    two versions; no other attachment is ever touched.
  * Off unless OWEN_AI_REPORT_ATTACH_ENABLED and OWEN_AI_PROPOSALS_ENABLED are on and the
    registry's owen_ai "zuper_proposals" level allows it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from ..zuper import client as zc
from ..zuper import config as zuper_config
from . import config, proposals, report, saved_reports

log = logging.getLogger(__name__)


class Refused(Exception):
    """A sentence for the person: nothing was written to Zuper."""


def refusal(db: Session) -> str | None:
    if not zuper_config.owen_ai_report_attach_enabled():
        return zuper_config.OWEN_AI_REPORT_ATTACH_OFF_SENTENCE
    return proposals.refusal(db)


def file_name(job_number: str | None, version: int) -> str:
    job = re.sub(r"[^A-Za-z0-9-]", "", str(job_number or ""))[:40] or "job"
    return "After-Inspection-Report-%s-v%d.pdf" % (job, version)


def _number(ref: Any) -> str | None:
    s = str(ref or "").strip().upper()
    m = re.fullmatch(r"(?:DTR[\s-]*)?(\d{1,8})", s)
    return "DTR-" + m.group(1) if m else None


def _pick(rows: list[dict], want: str) -> dict | None:
    digits = want.split("-")[-1]
    return next((x for x in rows if (report.proposal_number(x) or "").upper() == want
                 or str(x.get("estimate_no") or "") == digits), None)


def attach(db: Session, report_id: int, who: str, proposal: Any = None,
           number: int | None = None) -> dict:
    """Attach version `number` (default: the latest) of report `report_id` to its proposal.
    Raises Refused (nothing written) or returns {status: attached | already, ...}."""
    why = refusal(db)
    if why:
        raise Refused(why)
    row = saved_reports.get(db, report_id)
    v = saved_reports.version(db, row.id, number)
    pdf = saved_reports.report_dir(row.id) / ("v%d" % v.number) / "report.pdf"
    if not v.has_pdf or not pdf.is_file():
        raise Refused("Version %d of this report has no PDF, so there is nothing to attach."
                      % v.number)
    # Missing header values are left OUT of the page; say so, do not refuse (2026-11-12).
    notes = ["The report leaves out what is not set: " + w for w in v.warnings or []
             if config.PLACEHOLDER in w]
    data = pdf.read_bytes()
    if len(data) > zc.REPORT_ATTACH_MAX_BYTES:
        raise Refused("The PDF is %.1f MB; Zuper takes at most %d MB." % (
            len(data) / 1e6, zc.REPORT_ATTACH_MAX_BYTES // (1024 * 1024)))
    # The one named, else the report's own choice (its `proposal`), else the one Zuper gave
    # when the facts were read.
    want = _number(proposal) or _number((v.spec or {}).get("proposal")) \
        or _number((row.facts or {}).get("proposal"))
    if not want:
        raise Refused("This report is not linked to a proposal: name one, like DTR-168.")
    name = file_name(row.job_number, v.number)
    sent = False
    try:
        with zc.owen_ai_read():
            rows =[x for x in zc.rows_of(zc.request("GET", "/estimate", params={
                "filter.job": row.job_uid, "count": 50, "page": 1}))
                if isinstance(x, dict) and not x.get("is_deleted")]
        pick = _pick(rows, want)
        if pick is None:
            raise Refused("Job #%s has no proposal %s in Zuper; nothing was attached." % (
                row.job_number or row.job_uid, want))
        uid = str(pick["estimate_uid"])
        with zc.owen_ai_report_attach(uid):
            cur = zc.data_of(zc.request("GET", "/estimate/" + uid))
            cur = cur if isinstance(cur, dict) else {}
            job = cur.get("job")
            job_uid = job.get("job_uid") if isinstance(job, dict) else job
            if job_uid != row.job_uid:
                raise Refused("%s is not on job #%s; nothing was attached." % (
                    want, row.job_number or row.job_uid))
            if str(cur.get("estimate_status") or "").upper() != "DRAFT" \
                    or cur.get("is_proposal") is False or cur.get("is_deleted"):
                raise Refused("%s is not a DRAFT proposal (it is %s); nothing was attached." % (
                    want, cur.get("estimate_status")))
            have = [str(a.get("file_name") or "") for a in cur.get("attachments") or []
                    if isinstance(a, dict)]
            older = {str(a["attachment_uid"]): str(a.get("file_name") or "")
                     for a in cur.get("attachments") or [] if isinstance(a, dict)
                     and a.get("attachment_uid")
                     and str(a.get("file_name") or "").startswith(zc.REPORT_PREFIX)
                     and str(a.get("file_name") or "") != name}
            if name in have:
                return {"status": "already", "proposal": want, "file_name": name,
                        "version": v.number, "older": sorted(older.values()), "removed": [],
                        "notes": notes}
            sent = True
            up = zc.data_of(zc.request("POST", "/misc/upload",
                                       files={"attachment": (name, data, "application/pdf")},
                                       form={"attachment_type": "ATTACHMENT"}))
            if not isinstance(up, dict) or not up.get("attachment_uid") \
                    or not str(up.get("attachment_path") or "").startswith("https://"):
                raise zc.ZuperError("bad_response", "the upload answered without a file")
            zc.request("POST", "/note_attachment", body={
                "module_name": "ESTIMATE", "module_uid": uid, "data": {
                    "type": "ATTACHMENT", "attachments": [{
                        "attachment_uid": up["attachment_uid"], "file_name": name,
                        "file_size": up.get("attachment_size"), "url": up["attachment_path"],
                        "visible_to_customer": True}]}})
            back = zc.data_of(zc.request("GET", "/estimate/" + uid))
            back = back if isinstance(back, dict) else {}
            removed: list[str] = []
            if older and name in {str(a.get("file_name") or "")
                                  for a in back.get("attachments") or [] if isinstance(a, dict)}:
                # Replace: the old report PDF goes only once the new one is confirmed on it.
                zc.request("DELETE", "/note_attachment", params={
                    "item_uid": ",".join(older), "module_name": "ESTIMATE", "module_uid": uid,
                    "type": "ATTACHMENT"})
                back = zc.data_of(zc.request("GET", "/estimate/" + uid))
                back = back if isinstance(back, dict) else {}
                still = {str(a.get("attachment_uid")) for a in back.get("attachments") or []
                         if isinstance(a, dict)}
                removed = [n for u, n in older.items() if u not in still]
    except zc.ZuperError as exc:
        raise Refused("Nothing was attached to %s: %s%s" % (
            want, zc.sentence(exc), " (Zuper may have taken it - look at the proposal before "
                                    "trying again)." if sent else ".")) from None
    now = [str(a.get("file_name") or "") for a in back.get("attachments") or []
           if isinstance(a, dict)]
    problems = []
    if name not in now:
        problems.append("the file is not on it")
    if str(back.get("estimate_status") or "").upper() != "DRAFT":
        problems.append("its status is %s" % back.get("estimate_status"))
    log.info("owen-ai report %d v%d attached to %s by %s (%s)", row.id, v.number, want, who,
             "ok" if not problems else "; ".join(problems))
    if problems:
        raise Refused("The report was sent to %s but the read-back does not match: %s. Look at "
                      "the proposal in Zuper before doing anything else." % (
                          want, "; ".join(problems)))
    return {"status": "attached", "proposal": want, "file_name": name, "version": v.number,
            "older": sorted(n for n in older.values() if n not in removed), "removed": removed,
            "notes": notes}


def sentence(res: dict) -> str:
    s = ("%s (version %d) is now attached to %s, visible to the customer. It is still a DRAFT; "
         "nothing was sent." if res["status"] == "attached" else
         "%s (version %d) was already attached to %s; nothing was changed.") % (
             res["file_name"], res["version"], res["proposal"])
    if res.get("removed"):
        s += " Replaced the older report on it (%s)." % ", ".join(res["removed"])
    if res.get("older"):
        s += (" An older report is still on it (%s) and Zuper's Send ticks every attachment - "
              "remove it in Zuper." % ", ".join(res["older"]))
    for n in res.get("notes") or []:
        s += " " + n
    return s


# ------------------------------------------------------------------------------ the chat tool

WHO = "Owen (asked Owen AI)"


def tool_attach(args: dict) -> str:
    args = args if isinstance(args, dict) else {}
    with saved_reports._session() as db:
        try:
            row = saved_reports.find(db, str(args.get("report") or ""))
            res = attach(db, row.id, WHO, args.get("proposal"),
                         int(args["version"]) if args.get("version") else None)
        except (Refused, saved_reports.NotFound, LookupError, ValueError) as exc:
            return "ERROR: %s" % exc
        return sentence(res)


DESCRIPTION = (
    "ATTACH a saved after-inspection report's PDF to its proposal in Zuper - only when Owen asks. "
    "`report` = the report id or a job number; `proposal` = the DTR number (default: the one the "
    "report is linked to); `version` = which version (default: the latest). Only a DRAFT proposal "
    "on the same job. An older report PDF on it is replaced. The file is visible to the customer "
    "and goes with the proposal when someone sends it; this never sends anything.")

SPECS = (("attach_inspection_report", DESCRIPTION,
          {"type": "object", "properties": {
              "report": {"type": "string"}, "proposal": {"type": "string"},
              "version": {"type": "integer"}},
           "required": ["report"]},
          tool_attach),)
