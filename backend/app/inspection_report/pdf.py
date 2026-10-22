"""The report as a PDF, built on demand from the current draft (never stored). fpdf2, pure Python:
a cover block (company, customer, address, date), the summary, the findings, then the pictures
two to a page with their captions. Text is folded to Latin-1 for the built-in font (Spanish
accents survive; curly quotes and dashes become plain ones)."""
from __future__ import annotations

import io

from fpdf import FPDF

from .. import attachments
from ..dispatch import config as dc
from ..models import InspectionReport

COMPANY = "Dream Team Roofing"
SLATE = (47, 72, 88)
COPPER = (176, 90, 36)
INK = (29, 42, 51)
MUTED = (91, 104, 113)
FOLD = {chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"',
        chr(0x2013): "-", chr(0x2014): "-", chr(0x2026): "...", chr(0xA0): " "}
LABELS = {
    "en": {"report": "Roof inspection report", "for": "Prepared for", "date": "Inspection date",
           "summary": "Summary", "findings": "What we found", "photos": "Photos"},
    "es": {"report": "Informe de inspeccion del techo", "for": "Preparado para",
           "date": "Fecha de inspeccion", "summary": "Resumen", "findings": "Lo que encontramos",
           "photos": "Fotos"},
}


def _t(text) -> str:
    s = "".join(FOLD.get(ch, ch) for ch in str(text or ""))
    return s.encode("latin-1", "replace").decode("latin-1")


def build(r: InspectionReport) -> bytes:
    lab = LABELS.get(r.language or "en", LABELS["en"])
    pdf = FPDF(format="Letter", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    w = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_fill_color(*SLATE)
    pdf.rect(0, 0, pdf.w, 6, "F")
    pdf.set_y(16)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*COPPER)
    pdf.cell(w, 6, _t(COMPANY.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*INK)
    pdf.multi_cell(w, 9, _t(r.title or lab["report"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MUTED)
    when = dc.local(r.visit_at).strftime("%B %d, %Y") if r.visit_at else ""
    pdf.multi_cell(w, 5, _t("%s: %s%s\n%s: %s" % (
        lab["for"], r.customer_name or "", (" - " + r.address) if r.address else "",
        lab["date"], when)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    if r.summary:
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*SLATE)
        pdf.cell(w, 7, _t(lab["summary"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*INK)
        pdf.multi_cell(w, 5.6, _t(r.summary), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    if r.findings:
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*SLATE)
        pdf.cell(w, 7, _t(lab["findings"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*INK)
        for f in r.findings:
            pdf.multi_cell(w, 5.6, _t("-  " + f), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    photos = [p for p in (r.photos or []) if isinstance(p, dict) and p.get("sha")]
    for i, p in enumerate(photos):
        data = attachments.read("%s/%s" % (p["sha"][:2], p["sha"]))
        if not data:
            continue
        if i % 2 == 0:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(*SLATE)
            pdf.cell(w, 7, _t(lab["photos"]), new_x="LMARGIN", new_y="NEXT")
        img_h = 105
        y = pdf.get_y() + 2
        pdf.image(io.BytesIO(data), x=pdf.l_margin, y=y, w=w, h=img_h, keep_aspect_ratio=True)
        pdf.set_y(y + img_h + 2)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(w, 5, _t("%d. %s" % (i + 1, p.get("caption") or "")),
                       new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
