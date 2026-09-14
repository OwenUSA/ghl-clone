"""CompanyCam routes: the Photos tab, the contact panel, the image relay, the admin page.

Mounted by `main.py` as one router, so the app-level auth gate covers every route here.

## Who sees what

* **Viewing is any signed-in role.** A tech on the roof is exactly who needs the
  photos.
* **A card you cannot see has no photos you can see.** Every read resolves the card
  through `pipeline_access` and answers the same 404 a missing card gets. An image is
  served only when its project is linked to at least one card in a pipeline the reader
  can access, so a photo id cannot be used to reach a hidden card's pictures, and the
  contact panel lists only projects linked to the contact's VISIBLE cards.
* **Linking and the admin page are ADMIN.**

## The image relay, and why the browser never loads CompanyCam's URL itself

`GET /api/companycam/photos/{id}/{thumbnail|web}` streams the bytes. The browser never
sees a CompanyCam image URI, for three reasons that each hold on their own:

1. Nobody measured whether those URIs are signed, expiring or neither (there is no
   token on the build machine). If they do NOT expire they are bearer links to photos
   of customers' homes — forwardable, and alive after the viewer's session and access
   are gone. If they DO expire, a cached list hands the browser dead links. Relaying
   is right in both cases; handing out the URI is right in only one.
2. It is the only way per-pipeline access applies to the picture itself, not just to
   the list that names it.
3. It is the decision already made for Quo call recordings (DECISIONS.md, 2026-09-11):
   stream, do not redirect.

The cost is bandwidth through the CRM for four users, softened by
`Cache-Control: private` so a browser does not fetch the same thumbnail twice.
Nothing is written to disk or to the database.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, companycam, pipeline_access
from .db import get_db
from .models import (
    CompanyCamLink,
    CompanyCamProjectRequest,
    CompanyCamReviewItem,
    Contact,
    Opportunity,
    Pipeline,
)

router = APIRouter(tags=["companycam"])

IMAGE_CACHE = "private, max-age=3600"


def _active_links(stmt):
    return stmt.where(CompanyCamLink.unlinked_at.is_(None))


def _photo_out(photo: dict) -> dict:
    """What the browser gets for one photo. No CompanyCam URI, no token."""
    pid = str(photo["id"])
    return {
        "id": pid,
        "project_id": str(photo.get("project_id")) if photo.get("project_id") else None,
        "captured_at": companycam.iso_from_unix(photo.get("captured_at")
                                               or photo.get("created_at")),
        "creator_name": photo.get("creator_name"),
        "description": photo.get("description") or None,
        "annotated": companycam.preferred_uri(photo, "web")
        != companycam._uri(photo, "web"),
        "thumbnail_url": "/api/companycam/photos/%s/thumbnail" % pid,
        "image_url": "/api/companycam/photos/%s/web" % pid,
    }


def _project_out(project: dict, method: str | None) -> dict:
    return {
        "id": str(project["id"]),
        "name": project.get("name"),
        "address": companycam.project_address_line(project),
        "photo_count": project.get("photo_count"),
        "project_url": project.get("project_url"),
        "link_method": method,
    }


@router.get("/api/opportunities/{opp_id}/companycam")
def companycam_projects_for_opportunity(opp_id: int, refresh: bool = False,
                                        db: Session = Depends(get_db),
                                        principal: auth.Principal = auth.ANY_USER):
    """The card's linked projects, with CompanyCam's current name, link and count."""
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    if not companycam.enabled():
        return {"state": "off", "message": companycam.OFF, "projects": []}
    links = db.scalars(_active_links(select(CompanyCamLink).where(
        CompanyCamLink.opportunity_id == o.id)).order_by(CompanyCamLink.id)).all()
    projects = []
    for link in links:
        try:
            project = companycam.get_project(link.project_id, refresh=refresh)
        except companycam.CompanyCamError as exc:
            if exc.kind == "not_found":        # deleted in CompanyCam: nothing to show
                continue
            return {"state": "unavailable", "message": companycam.UNAVAILABLE,
                    "projects": []}
        projects.append((companycam._unix(project.get("created_at")) or 0,
                         _project_out(project, link.method)))
    # Newest project first, so a card with several opens on the latest job.
    projects.sort(key=lambda pair: pair[0], reverse=True)
    return {"state": "ok", "message": None, "projects": [p for _, p in projects]}


@router.get("/api/opportunities/{opp_id}/companycam/projects/{project_id}/photos")
def companycam_project_photos(opp_id: int, project_id: str,
                              page: int = Query(1, ge=1, le=companycam.MAX_PAGES),
                              refresh: bool = False,
                              db: Session = Depends(get_db),
                              principal: auth.Principal = auth.ANY_USER):
    """One page (50) of a linked project's photos, newest first."""
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    linked = db.scalar(_active_links(select(CompanyCamLink.id).where(
        CompanyCamLink.opportunity_id == o.id, CompanyCamLink.project_id == project_id)))
    if linked is None:
        raise HTTPException(404, "project not found")
    if not companycam.enabled():
        return {"state": "off", "message": companycam.OFF, "photos": [],
                "page": page, "has_more": False}
    try:
        rows = companycam.photo_page(project_id, page, refresh=refresh)
    except companycam.CompanyCamError:
        return {"state": "unavailable", "message": companycam.UNAVAILABLE, "photos": [],
                "page": page, "has_more": False}
    return {"state": "ok", "message": None, "photos": [_photo_out(p) for p in rows],
            "page": page, "has_more": len(rows) >= companycam.PER_PAGE}


@router.get("/api/companycam/photos/{photo_id}/{variant}")
def companycam_photo_image(photo_id: str, variant: Literal["thumbnail", "web"],
                           db: Session = Depends(get_db),
                           principal: auth.Principal = auth.ANY_USER) -> Response:
    """The picture itself, relayed. See the module docstring for why."""
    if not companycam.enabled():
        raise HTTPException(503, companycam.OFF)
    try:
        photo = companycam.get_photo(photo_id)
    except companycam.CompanyCamError as exc:
        raise HTTPException(404 if exc.kind == "not_found" else 502,
                            "photo not found" if exc.kind == "not_found"
                            else companycam.UNAVAILABLE) from None
    project_id = str(photo.get("project_id") or "")
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    stmt = _active_links(select(CompanyCamLink.id).join(
        Opportunity, Opportunity.id == CompanyCamLink.opportunity_id).where(
        CompanyCamLink.project_id == project_id))
    if hidden:
        stmt = stmt.where(Opportunity.pipeline_id.not_in(hidden))
    if not project_id or db.scalar(stmt.limit(1)) is None:
        raise HTTPException(404, "photo not found")
    uri = companycam.preferred_uri(photo, variant)
    if not uri:
        raise HTTPException(404, "photo not found")
    try:
        data, ctype = companycam.fetch_image(uri)
    except companycam.CompanyCamError as exc:
        raise HTTPException(404 if exc.kind == "not_found" else 502,
                            companycam.UNAVAILABLE) from None
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": IMAGE_CACHE,
                             "X-Content-Type-Options": "nosniff"})


@router.get("/api/contacts/{contact_id}/companycam")
def companycam_projects_for_contact(contact_id: int, db: Session = Depends(get_db),
                                    principal: auth.Principal = auth.ANY_USER):
    """Every project linked to this customer's cards that the reader can see. Names
    come from the link rows, so the panel costs CompanyCam nothing."""
    if db.get(Contact, contact_id) is None:
        raise HTTPException(404, "contact not found")
    if not companycam.enabled():
        return {"state": "off", "projects": []}
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    stmt = _active_links(select(CompanyCamLink, Opportunity).join(
        Opportunity, Opportunity.id == CompanyCamLink.opportunity_id).where(
        Opportunity.contact_id == contact_id))
    stmt = pipeline_access.visible_opportunities(stmt, hidden)
    projects: dict[str, dict] = {}
    for link, o in db.execute(stmt.order_by(CompanyCamLink.id.desc())).all():
        p = projects.setdefault(link.project_id, {
            "id": link.project_id, "name": link.project_name, "opportunities": []})
        p["opportunities"].append({"id": o.id, "title": o.title})
    return {"state": "ok", "projects": list(projects.values())}


# ------------------------------------------------------------------ admin

@router.get("/api/companycam/status")
def companycam_status(db: Session = Depends(get_db), _: auth.Principal = auth.ADMIN):
    """Configuration (never the token), the hourly check's heartbeat, and totals."""
    by_method = dict(db.execute(_active_links(select(
        CompanyCamLink.method, func.count(CompanyCamLink.id))).group_by(
        CompanyCamLink.method)).all())
    by_state = dict(db.execute(select(
        CompanyCamProjectRequest.state, func.count(CompanyCamProjectRequest.id)).group_by(
        CompanyCamProjectRequest.state)).all())
    review = db.scalar(select(func.count(CompanyCamReviewItem.id)).where(
        CompanyCamReviewItem.dismissed_at.is_(None))) or 0
    return {**companycam.config_summary(), "heartbeat": companycam.heartbeat(db),
            "links_by_method": by_method, "project_requests": by_state,
            "review_open": review}


def _review_out(db: Session, item: CompanyCamReviewItem) -> dict:
    cards = []
    for oid in item.candidate_opportunity_ids or []:
        o = db.get(Opportunity, oid)
        if o is None:
            continue
        pipeline = db.get(Pipeline, o.pipeline_id)
        street, _ = companycam.card_address(o)
        cards.append({"id": o.id, "title": o.title,
                      "contact_name": o.contact.name if o.contact else None,
                      "pipeline_name": pipeline.name if pipeline else None,
                      "address": street})
    return {"id": item.id, "project_id": item.project_id, "project_name": item.project_name,
            "project_address": item.project_address, "candidates": cards,
            "first_seen_at": companycam._aware(item.first_seen_at).isoformat()}


@router.get("/api/companycam/review")
def companycam_review_list(db: Session = Depends(get_db), _: auth.Principal = auth.ADMIN):
    """Projects that matched a customer by NAME only. Not linked until an admin says."""
    items = db.scalars(select(CompanyCamReviewItem).where(
        CompanyCamReviewItem.dismissed_at.is_(None)).order_by(
        CompanyCamReviewItem.id)).all()
    return [_review_out(db, i) for i in items]


class ReviewLink(BaseModel):
    opportunity_ids: list[int] = Field(min_length=1, max_length=50)


def _review_item(db: Session, item_id: int) -> CompanyCamReviewItem:
    item = db.get(CompanyCamReviewItem, item_id)
    if item is None or item.dismissed_at is not None:
        raise HTTPException(404, "review item not found")
    return item


@router.post("/api/companycam/review/{item_id}/link")
def companycam_review_link(item_id: int, body: ReviewLink, db: Session = Depends(get_db),
                           principal: auth.Principal = auth.ADMIN):
    item = _review_item(db, item_id)
    ids = sorted(set(body.opportunity_ids))
    missing = [i for i in ids if db.get(Opportunity, i) is None]
    if missing:
        raise HTTPException(400, "unknown opportunity ids: %s" % missing)
    for oid in ids:
        link = db.scalar(select(CompanyCamLink).where(
            CompanyCamLink.opportunity_id == oid, CompanyCamLink.project_id == item.project_id))
        if link is None:
            db.add(CompanyCamLink(opportunity_id=oid, project_id=item.project_id,
                                  method="manual", project_name=item.project_name,
                                  linked_by=principal.email))
        elif link.unlinked_at is not None:
            link.unlinked_at, link.method, link.linked_by = None, "manual", principal.email
    project_id = item.project_id
    db.delete(item)
    db.commit()
    return {"project_id": project_id, "linked": ids}


@router.post("/api/companycam/review/{item_id}/dismiss")
def companycam_review_dismiss(item_id: int, db: Session = Depends(get_db),
                              _: auth.Principal = auth.ADMIN):
    item = _review_item(db, item_id)
    item.dismissed_at = datetime.now(UTC)
    db.commit()
    return {"dismissed": item.id}


@router.delete("/api/opportunities/{opp_id}/companycam/projects/{project_id}")
def companycam_unlink(opp_id: int, project_id: str, db: Session = Depends(get_db),
                      principal: auth.Principal = auth.ADMIN):
    """Take a wrongly linked project off a card. Only the link: CompanyCam is not
    touched. The row stays, marked, so the address rule never links it back."""
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    link = db.scalar(_active_links(select(CompanyCamLink).where(
        CompanyCamLink.opportunity_id == o.id, CompanyCamLink.project_id == project_id)))
    if link is None:
        raise HTTPException(404, "project not found")
    link.unlinked_at = datetime.now(UTC)
    db.commit()
    return {"unlinked": project_id, "opportunity_id": o.id}
