"""Auditable PDF and evidence bundle, generated from one immutable run."""

import json
import zipfile
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
)
from .storage import digest


def make_report(result, out):
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="SmallNote",
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#475569"),
        )
    )
    story = []

    def p(text, style="BodyText"):
        return Paragraph(escape(str(text)), styles[style])

    def heading(text):
        story.extend([Spacer(1, 14), p(text, "Heading2")])

    story += [
        p("OCEAN-EYE", "Title"),
        p("Maritime environmental investigation", "Heading2"),
        p(result["data_label"], "Heading3"),
        p(f"Case {result['case_id']} | Run {result['run_id']}", "SmallNote"),
        p(f"Observation: {result['observation_time']}", "SmallNote"),
        p(f"Analysis SHA-256: {result['analysis_hash']}", "SmallNote"),
    ]
    heading("Investigation summary")
    spill = result["spill"]
    origin = result["origin"]
    ranking = result["attribution"]["ranking"]
    for text in [
        f"Dark-region candidate area: {spill['area_km2']:.2f} km2; perimeter: {spill['perimeter_km']:.2f} km.",
        f"Assumed release window: {' to '.join(origin['release_window'])}.",
        f"Modeled origin: {origin['centroid'][1]:.4f} N, {origin['centroid'][0]:.4f} E; conditional 90% radius: {origin['radius90_km']:.2f} km.",
        (
            f"Highest-ranked candidate: {ranking[0]['name']} ({ranking[0]['mmsi']}), screening score {ranking[0]['score']:.1f}/100."
            if ranking
            else "No ranked candidates."
        ),
        "Scores are investigative priorities, not probabilities of guilt or evidence of discharge.",
    ]:
        story.append(p(text))
    story.extend(
        [
            Spacer(1, 12),
            Image(str(out / "satellite.png"), width=2.65 * inch, height=2.65 * inch),
            p(
                "Processed input preview. Dark regions require independent oil confirmation.",
                "SmallNote",
            ),
        ]
    )
    heading("Candidate ranking")
    table = [
        [
            p("Vessel", "SmallNote"),
            p("MMSI", "SmallNote"),
            p("Score /100", "SmallNote"),
            p("Origin distance", "SmallNote"),
        ]
    ]
    for v in ranking[:8]:
        table.append(
            [
                p(v["name"], "SmallNote"),
                p(v["mmsi"], "SmallNote"),
                p(v["score"], "SmallNote"),
                p(f"{v['nearest_km']} km", "SmallNote"),
            ]
        )
    tbl = Table(table, colWidths=[170, 90, 85, 110], repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(tbl)
    story.append(PageBreak())
    heading("Supporting and contradicting evidence")
    if ranking:
        for text in ranking[0]["supporting"]:
            story.append(p("Supporting: " + text))
        for text in ranking[0]["contradicting"]:
            story.append(p("Contradicting / limiting: " + text))
    heading("Forecast and exposure")
    for r in result["impact"]["receptors"]:
        story.append(
            p(
                f"{r['name']} [{r['source_type']}]: "
                + (
                    f"first envelope overlap at {r['first_overlap_h']:g} h"
                    if r["first_overlap_h"]
                    else "no modeled envelope overlap"
                )
            )
        )
    heading("Methods, uncertainty and limitations")
    for text in result["limitations"]:
        story.append(p(text))
    heading("Input provenance")
    for source in result["provenance"]["inputs"]:
        story.extend(
            [
                p(
                    f"{source['role']}: {source['source_type']} | {source['name']}",
                    "SmallNote",
                ),
                p(f"SHA-256: {source['sha256']}", "SmallNote"),
            ]
        )
    heading("Next data to obtain")
    for text in result["recommendations"]:
        story.append(p(text))

    def footer(c, doc):
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawString(42, 27, result["data_label"] + " | Investigative screening only")
        c.drawRightString(553, 27, f"Page {doc.page}")

    SimpleDocTemplate(
        str(out / "report.pdf"),
        pagesize=(595, 842),
        rightMargin=42,
        leftMargin=42,
        topMargin=36,
        bottomMargin=45,
    ).build(story, onFirstPage=footer, onLaterPages=footer)


def bundle(out):
    files = [
        p
        for p in out.iterdir()
        if p.is_file() and p.name not in ("evidence.zip", "checksums.sha256")
    ]
    manifest = "\n".join(f"{digest(p)}  {p.name}" for p in sorted(files)) + "\n"
    (out / "checksums.sha256").write_text(manifest, encoding="utf-8")
    with zipfile.ZipFile(out / "evidence.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in files + [out / "checksums.sha256"]:
            z.write(p, p.name)
