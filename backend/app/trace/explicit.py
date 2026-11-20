"""Explicit trace links: a requirement's ID written next to code, a test, a commit or a pull request (Step 10).

Plain code (rule 4), confidence 1.0, origin "explicit". Only approved requirements get links; an ID of an unknown
or unapproved requirement is ignored. A link that exists is never changed, except that an AI suggestion nobody
decided on becomes explicit when the ID is found for it (stronger evidence).

| Where the ID is                                   | Link                                  |
| commit message                                    | implements commit                     |
| PR title, description or head branch              | implements pr                         |
| branch name without a pull request                | implements commit (the branch head)   |
| comment or docstring in a code file               | implements file                       |
| test name, its docstring or a comment inside it   | verifies test_function                |
| anywhere else in a test file                      | verifies test_file                    |
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import RepoCommit, RepoPull, RepoSnapshot, Repository, Requirement, TraceLink
from app.models.requirement import requirement_code
from app.trace.ids import find_req_numbers
from app.trace.targets import Target, commit_target, pull_target

WHERE_LABELS = {"comment": "a comment", "docstring": "a docstring", "name": "the name"}


@dataclass
class Found:
    requirement: Requirement
    target: Target
    evidence: str


def _found_in_text(
    approved: dict[int, Requirement], text: str | None, target: Target, where: str, out: list[Found]
) -> None:
    for number in find_req_numbers(text):
        requirement = approved.get(number)
        if requirement is not None:
            out.append(Found(requirement, target, f"{requirement_code(number)} in {where}"))


def find_explicit(
    approved: dict[int, Requirement],
    snapshot: RepoSnapshot | None,
    commits: list[RepoCommit],
    pulls: list[RepoPull],
    branches: list[dict],
) -> list[Found]:
    """Every (requirement, target) pair whose ID was found, with where it was found. Pure: no database writes."""
    found: list[Found] = []
    for commit in commits:
        _found_in_text(approved, commit.message, commit_target(commit), "the commit message", found)

    pull_branches = set()
    for pull in pulls:
        target = pull_target(pull)
        _found_in_text(approved, pull.title, target, "the pull request title", found)
        _found_in_text(approved, pull.body, target, "the pull request description", found)
        _found_in_text(approved, pull.head_ref, target, f"the branch name {pull.head_ref}", found)
        pull_branches.add(pull.head_ref)

    by_sha = {c.sha: c for c in commits}
    for branch in branches:
        name, sha = branch.get("name") or "", branch.get("sha") or ""
        if not sha or name in pull_branches:
            continue
        commit = by_sha.get(sha)
        target = (
            commit_target(commit)
            if commit
            else Target("commit", sha, f"{sha[:7]} (head of branch {name})", None, None, name)
        )
        _found_in_text(approved, name, target, f"the branch name {name}", found)

    for f in snapshot.files if snapshot else []:
        symbols = {s["qualname"]: s for s in f.symbols}
        for m in f.mentions:
            requirement = approved.get(m["number"])
            if requirement is None:
                continue
            symbol = symbols.get(m["symbol"]) if m.get("symbol") else None
            where = f"{WHERE_LABELS.get(m['where'], m['where'])} (line {m['line']})"
            if f.kind == "test" and symbol is not None and symbol["kind"] == "test":
                doc = (symbol.get("docstring") or "").strip()
                key = f"{f.path}::{symbol['qualname']}"
                target = Target(
                    "test_function", key, key, doc.splitlines()[0][:300] if doc else None, symbol["line_start"]
                )
            elif f.kind == "test":
                target = Target("test_file", f.path, f.path, f.docstring, m["line"])
            else:
                target = Target("file", f.path, f.path, f.docstring, m["line"])
            found.append(Found(requirement, target, f"{requirement_code(m['number'])} in {where}"))
    return found


def add_explicit_links(
    db: Session,
    project_id: int,
    repo: Repository,
    found: list[Found],
    existing: dict[tuple[int, str, str], TraceLink],
) -> int:
    """Store new explicit links; `existing` (requirement id, target type, key) -> link is updated in place.

    Returns how many links were added or became explicit. The caller commits.
    """
    added = 0
    for item in found:
        key = (item.requirement.id, item.target.target_type, item.target.key)
        link = existing.get(key)
        if link is not None:
            # Only an AI suggestion nobody decided on is upgraded; a person's decision is never changed (rule 2)
            if link.origin == "ai" and link.status in ("suggested", "dismissed"):
                link.origin, link.status, link.confidence = "explicit", "suggested", 1.0
                link.evidence, link.reason, link.llm_checked = item.evidence, explicit_reason(item), True
                link.requirement_version = item.requirement.current.version
                added += 1
            continue
        link = TraceLink(
            project_id=project_id,
            repository_id=repo.id,
            requirement_id=item.requirement.id,
            link_type=item.target.link_type,
            target_type=item.target.target_type,
            target_key=item.target.key,
            target_label=item.target.label,
            target_detail=item.target.detail,
            target_line=item.target.line,
            origin="explicit",
            status="suggested",
            confidence=1.0,
            reason=explicit_reason(item),
            evidence=item.evidence,
            llm_checked=True,
            requirement_version=item.requirement.current.version,
        )
        db.add(link)
        existing[key] = link
        added += 1
    return added


def explicit_reason(item: Found) -> str:
    return f"The requirement ID {item.requirement.code} is written there."
