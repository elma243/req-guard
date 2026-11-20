"""What a requirement can be linked to, built from the stored analysis (Step 10).

A target has a stable key (see app/models/trace.py), a label for people, a detail line (docstring or title), a text
for embedding similarity, and a GitHub URL. Explicit links, suggestions, manual links, the target search and the
matrix all use these, so the same file, test, commit or PR always has the same key and URL.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import RepoCommit, RepoPull, RepoSnapshot, Repository
from app.models.trace import TARGET_LINK_TYPE

# Characters of a commit message or PR body used for similarity and the LLM
TEXT_CHARS = 500
_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass
class Target:
    target_type: str  # file | test_file | test_function | commit | pr
    key: str
    label: str
    detail: str | None = None
    line: int | None = None
    # Text compared with the requirement statement (embeddings) and shown to the LLM
    text: str = ""

    @property
    def link_type(self) -> str:
        return TARGET_LINK_TYPE[self.target_type]


def words(name: str) -> str:
    """Identifier or path as words: "app/auth/resetPassword_view.py" -> "app auth reset Password view py"."""
    return " ".join(w for w in _SPLIT.split(_CAMEL.sub(" ", name)) if w and w != "py")


def latest_snapshot(db: Session, repo: Repository) -> RepoSnapshot | None:
    """The snapshot of the head commit of the last analysis."""
    if not repo.head_sha:
        return None
    return db.scalar(
        select(RepoSnapshot)
        .where(RepoSnapshot.repository_id == repo.id, RepoSnapshot.commit_sha == repo.head_sha)
        .options(selectinload(RepoSnapshot.files))
    )


def file_targets(snapshot: RepoSnapshot | None) -> list[Target]:
    """Code files, test files and test functions of the snapshot."""
    if snapshot is None:
        return []
    targets: list[Target] = []
    for f in snapshot.files:
        if f.kind == "other":
            continue
        names = " ".join(words(s["name"]) for s in f.symbols if s["kind"] != "test")
        if f.kind == "code":
            text = " ".join(p for p in (words(f.path), names, f.docstring or "") if p)
            targets.append(Target("file", f.path, f.path, f.docstring, None, text))
            continue
        targets.append(Target("test_file", f.path, f.path, f.docstring, None, words(f.path)))
        for s in f.symbols:
            if s["kind"] != "test":
                continue
            doc = (s.get("docstring") or "").strip()
            first = doc.splitlines()[0][:300] if doc else None
            text = " ".join(p for p in (words(s["name"]), doc[:TEXT_CHARS], words(f.path)) if p)
            key = f"{f.path}::{s['qualname']}"
            targets.append(Target("test_function", key, key, first, s["line_start"], text))
    return targets


def commit_target(commit: RepoCommit) -> Target:
    return Target(
        "commit",
        commit.sha,
        f"{commit.sha[:7]} {commit.title}".strip(),
        commit.title or None,
        None,
        (commit.message or "")[:TEXT_CHARS],
    )


def pull_target(pull: RepoPull) -> Target:
    text = f"{pull.title}\n{pull.body or ''}"[:TEXT_CHARS]
    return Target("pr", str(pull.number), f"#{pull.number} {pull.title}".strip(), pull.title or None, None, text)


def all_targets(db: Session, repo: Repository, snapshot: RepoSnapshot | None = None) -> list[Target]:
    """Every target of the repository: files and tests of the latest snapshot, commits and pull requests."""
    snapshot = snapshot if snapshot is not None else latest_snapshot(db, repo)
    commits = db.scalars(
        select(RepoCommit).where(RepoCommit.repository_id == repo.id).order_by(RepoCommit.committed_at.desc())
    ).all()
    pulls = db.scalars(select(RepoPull).where(RepoPull.repository_id == repo.id).order_by(RepoPull.number.desc())).all()
    return file_targets(snapshot) + [commit_target(c) for c in commits] + [pull_target(p) for p in pulls]


def github_url(repo: Repository, target_type: str, key: str, line: int | None = None) -> str:
    """Where the target opens on GitHub. Files open at the analysed head commit, so the lines match."""
    base = repo.html_url
    if target_type == "commit":
        return f"{base}/commit/{key}"
    if target_type == "pr":
        return f"{base}/pull/{key}"
    path = key.split("::", 1)[0]
    ref = repo.head_sha or repo.default_branch
    return f"{base}/blob/{ref}/{path}" + (f"#L{line}" if line else "")
