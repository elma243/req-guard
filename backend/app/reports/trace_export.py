"""Export of the traceability matrix as CSV (Step 10): one row per approved requirement.

Links are written with their origin and status, so a reader sees which ones a person confirmed and which are
only explicit ID matches or AI suggestions. Rejected links are left out.
"""

import csv
import io
from datetime import datetime

from app.reports.test_case_export import csv_cell, stamp
from app.schemas.trace import TraceLinkOut, TraceMatrix

CSV_HEADER = [
    "Requirement",
    "Statement",
    "Module",
    "Coverage",
    "Coverage reason",
    "Code links",
    "Test links",
    "Matched test cases",
    "Criteria with a linked test",
]

COVERAGE_LABELS = {
    "not_started": "Not started",
    "code_only": "Code only",
    "tests_only": "Tests only",
    "code_and_tests": "Code + tests",
    "verified": "Verified",
}
ORIGIN_LABELS = {"explicit": "ID match", "ai": "AI suggestion", "user": "added by a person"}
STATUS_LABELS = {"suggested": "not confirmed", "accepted": "confirmed"}


def link_text(link: TraceLinkOut) -> str:
    parts = [ORIGIN_LABELS[link.origin], STATUS_LABELS.get(link.status, link.status)]
    if link.confidence is not None:
        parts.append(f"confidence {link.confidence:.2f}")
    return f"{link.target_label} [{', '.join(parts)}]"


def _links(links: list[TraceLinkOut]) -> str:
    return "; ".join(link_text(link) for link in links if link.status != "rejected")


def build_csv(project_name: str, at: datetime, matrix: TraceMatrix) -> bytes:
    """UTF-8 with a byte order mark, so spreadsheet programs show every language correctly."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Project", csv_cell(project_name)])
    writer.writerow(["Repository", csv_cell(matrix.repo.full_name if matrix.repo else "not connected")])
    if matrix.repo and matrix.repo.head_sha:
        writer.writerow(["Commit", matrix.repo.head_sha])
    writer.writerow(["Exported", stamp(at)])
    writer.writerow([])
    writer.writerow(CSV_HEADER)
    for row in matrix.rows:
        cases = sorted({c for link in row.verifies if link.live for c in link.test_cases})
        covered = sum(1 for c in row.criteria if c.covered)
        writer.writerow(
            [
                csv_cell(v)
                for v in (
                    row.requirement.code,
                    row.requirement.statement,
                    row.requirement.module_name or "",
                    COVERAGE_LABELS[row.coverage],
                    row.coverage_reason,
                    _links(row.implements),
                    _links(row.verifies),
                    " ".join(cases),
                    f"{covered}/{len(row.criteria)}" if row.criteria else "",
                )
            ]
        )
    return ("﻿" + out.getvalue()).encode("utf-8")
