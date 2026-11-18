"""Export of the project's tasks as CSV (Step 9): one row per task, grouped by task group.

The suggestion is exported as a suggestion: "Assignee" is only filled once the lead confirmed someone.
"""

import csv
import io
from datetime import datetime

from app.models import Task, TaskGroup
from app.reports.test_case_export import csv_cell, stamp

CSV_HEADER = [
    "Group",
    "Module",
    "Task",
    "Requirement",
    "Statement",
    "Status",
    "Assignee",
    "Suggested assignee",
    "Suggestion reason",
    "Needs review",
    "Accepted test cases",
    "Jira issue",
]

STATUS_LABELS = {"suggested": "Not assigned", "assigned": "Assigned", "in_progress": "In progress", "done": "Done"}


def accepted_test_codes(task: Task) -> list[str]:
    return [t.code for t in task.requirement.test_cases if t.status in ("accepted", "edited")]


def _row(group: TaskGroup, task: Task) -> list[object]:
    suggestion_pending = task.has_pending_suggestion
    return [
        group.name,
        group.module_name or "",
        task.code,
        task.requirement.code,
        task.requirement.current.statement,
        STATUS_LABELS[task.status],
        task.assignee.name if task.assignee else "",
        task.suggested_assignee.name if suggestion_pending and task.suggested_assignee else "",
        task.suggestion_reason if suggestion_pending else "",
        f"yes: {task.needs_review_reason}" if task.needs_review else "",
        " ".join(accepted_test_codes(task)),
        task.jira_issue_key or "",
    ]


def build_csv(project_name: str, at: datetime, groups: list[TaskGroup]) -> bytes:
    """UTF-8 with a byte order mark, so spreadsheet programs show every language correctly."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Project", csv_cell(project_name)])
    writer.writerow(["Exported", stamp(at)])
    writer.writerow([])
    writer.writerow(CSV_HEADER)
    for group in groups:
        for task in group.tasks:
            writer.writerow([csv_cell(v) for v in _row(group, task)])
    return ("﻿" + out.getvalue()).encode("utf-8")
