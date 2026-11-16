"""Export of approved requirements with their accepted acceptance criteria and test cases (FR-10), as CSV and PDF.

Only items a person accepted or edited are exported; AI suggestions nobody reviewed and rejected items are not.
An item written for an older requirement version is exported with a "needs review" mark.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Requirement

REVIEWED = ("accepted", "edited")
NOTE = (
    "Generated with AI assistance and reviewed by the team. It lists the planned checks; it does not show that "
    "the system is complete or ready to release."
)
NO_TESTS = "No accepted test cases yet"
CSV_HEADER = [
    "Requirement",
    "Statement",
    "Version",
    "Criterion",
    "Criterion text",
    "Test case",
    "Title",
    "Type",
    "Given",
    "When",
    "Then",
    "Status",
    "Needs review",
]


@dataclass
class ExportCriterion:
    id: int
    code: str
    text: str
    needs_review: bool


@dataclass
class ExportTestCase:
    code: str
    title: str
    type: str
    given: str
    when: str
    then: str
    criterion_id: int | None
    criterion_code: str | None
    status: str
    needs_review: bool


@dataclass
class ExportRequirement:
    code: str
    statement: str
    version: int
    criteria: list[ExportCriterion] = field(default_factory=list)
    test_cases: list[ExportTestCase] = field(default_factory=list)


def export_rows(requirements: list[Requirement]) -> list[ExportRequirement]:
    """Approved requirements in code order, each with its reviewed criteria and test cases."""
    rows = []
    for r in sorted(requirements, key=lambda r: r.number):
        if r.approval is None:
            continue
        codes = {c.id: c.code for c in r.criteria}
        rows.append(
            ExportRequirement(
                code=r.code,
                statement=r.current.statement,
                version=r.current.version,
                criteria=[
                    ExportCriterion(id=c.id, code=c.code, text=c.current.text, needs_review=c.needs_review)
                    for c in r.criteria
                    if c.status in REVIEWED
                ],
                test_cases=[
                    ExportTestCase(
                        code=t.code,
                        title=t.current.title,
                        type=t.current.type,
                        given=t.current.given,
                        when=t.current.when,
                        then=t.current.then,
                        criterion_id=t.current.criterion_id,
                        criterion_code=codes.get(t.current.criterion_id),
                        status=t.status,
                        needs_review=t.needs_review,
                    )
                    for t in r.test_cases
                    if t.status in REVIEWED
                ],
            )
        )
    return rows


def export_filename(project_name: str, at: datetime, extension: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")[:40] or "project"
    return f"{slug}-test-cases-{at:%Y-%m-%d}.{extension}"


def _stamp(at: datetime) -> str:
    return f"{at:%Y-%m-%d %H:%M} UTC"


# ---------- CSV ----------


def _cell(value: object) -> str:
    """A spreadsheet must not run text from the client as a formula (CSV injection)."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_csv(project_name: str, at: datetime, rows: list[ExportRequirement]) -> bytes:
    """UTF-8 with a byte order mark, so spreadsheet programs show every language correctly."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Project", _cell(project_name)])
    writer.writerow(["Exported", _stamp(at)])
    writer.writerow([])
    writer.writerow(CSV_HEADER)
    for r in rows:
        base = [r.code, r.statement, r.version]

        def write(criterion: ExportCriterion | None, test: ExportTestCase | None, base: list = base) -> None:
            crit = [criterion.code, criterion.text] if criterion else ["", ""]
            if test:
                cells = [test.code, test.title, test.type, test.given, test.when, test.then, test.status]
                review = test.needs_review or bool(criterion and criterion.needs_review)
            else:
                cells = ["", NO_TESTS if criterion is None else "", "", "", "", "", ""]
                review = bool(criterion and criterion.needs_review)
            writer.writerow([_cell(v) for v in [*base, *crit, *cells, "yes" if review else ""]])

        exported = {c.id for c in r.criteria}
        for criterion in r.criteria:
            tests = [t for t in r.test_cases if t.criterion_id == criterion.id]
            for test in tests or [None]:
                write(criterion, test)
        for test in [t for t in r.test_cases if t.criterion_id not in exported]:
            write(None, test)
        if not r.criteria and not r.test_cases:
            write(None, None)
    return ("﻿" + out.getvalue()).encode("utf-8")


# ---------- PDF ----------


def _latin(text: str) -> str:
    """The PDF's built-in fonts cover Latin text only; other characters become "?" instead of failing."""
    return escape(text.encode("cp1252", "replace").decode("cp1252"))


def build_pdf(project_name: str, at: datetime, rows: list[ExportRequirement]) -> bytes:
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11)
    note = ParagraphStyle("note", parent=small, textColor=colors.HexColor("#555555"))
    heading = styles["Heading3"]

    story: list = [
        Paragraph("Acceptance criteria and test cases", styles["Title"]),
        Paragraph(f"Project: {_latin(project_name)}", body),
        Paragraph(f"Exported: {_stamp(at)}", body),
        Spacer(1, 3 * mm),
        Paragraph(NOTE, note),
        Spacer(1, 4 * mm),
    ]
    if not rows:
        story.append(Paragraph("No approved requirements yet.", body))

    for r in rows:
        block: list = [
            Paragraph(f"{r.code} (version {r.version})", heading),
            Paragraph(_latin(r.statement), body),
        ]
        if r.criteria:
            block.append(Paragraph("<b>Acceptance criteria</b>", small))
            for c in r.criteria:
                mark = " <i>(needs review)</i>" if c.needs_review else ""
                block.append(Paragraph(f"<b>{c.code}</b> {_latin(c.text)}{mark}", small))
        block.append(Spacer(1, 2 * mm))
        story.append(KeepTogether(block))

        if not r.test_cases:
            story += [Paragraph(f"<i>{NO_TESTS}.</i>", small), Spacer(1, 4 * mm)]
            continue
        table = [[Paragraph(f"<b>{h}</b>", small) for h in ("Test case", "Type", "Steps", "Criterion")]]
        for t in r.test_cases:
            mark = "<br/><i>needs review</i>" if t.needs_review else ""
            steps = (
                f"<b>{_latin(t.title)}</b><br/><b>Given</b> {_latin(t.given)}<br/>"
                f"<b>When</b> {_latin(t.when)}<br/><b>Then</b> {_latin(t.then)}"
            )
            table.append(
                [
                    Paragraph(f"{t.code}{mark}", small),
                    Paragraph(t.type, small),
                    Paragraph(steps, small),
                    Paragraph(t.criterion_code or "-", small),
                ]
            )
        grid = Table(table, colWidths=[22 * mm, 18 * mm, 112 * mm, 18 * mm], repeatRows=1)
        grid.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story += [grid, Spacer(1, 5 * mm)]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Test cases - {project_name.encode('cp1252', 'replace').decode('cp1252')}",
    )
    doc.build(story)
    return buffer.getvalue()
