"""Repository analysis (Step 10): read what is new on GitHub, store it, then recover trace links.

`analyze()`:
1. Head commit of the default branch.
2. New commits (stopping at the first stored SHA), with their changed files while the rate limit allows (the rest
   are filled in by a later analysis); pull requests updated since the last analysis; branches.
3. When the head commit has no snapshot yet: download its tarball once and store the parsed files. An unchanged
   head downloads nothing.
4. `recover_links()`: explicit links, then suggestions (app/trace/).

`recover_links()` alone uses only what is stored, so it makes no GitHub request (e.g. after approving more
requirements).
"""

import tarfile
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.llm import LLMClient
from app.code.github import GitHubClient
from app.code.parser import parse_tarball
from app.connectors.http import ConnectorError
from app.core.config import settings
from app.models import (
    AcceptanceCriterion,
    RepoCommit,
    RepoFile,
    RepoPull,
    RepoSnapshot,
    Repository,
    Requirement,
    TestCase,
    TraceLink,
)
from app.schemas.trace import AnalysisResult, RecoverResult
from app.trace.explicit import add_explicit_links, find_explicit
from app.trace.suggest import suggest_links
from app.trace.targets import all_targets, latest_snapshot

# Commit file lists read per analysis (one request each)
MAX_COMMIT_FILE_REQUESTS = 100


def approved_requirements(db: Session, project_id: int) -> list[Requirement]:
    """Approved requirements of the project, by number, with what links and coverage need."""
    requirements = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project_id)
        .order_by(Requirement.number)
        .options(
            selectinload(Requirement.versions),
            selectinload(Requirement.approvals),
            selectinload(Requirement.criteria).selectinload(AcceptanceCriterion.versions),
            selectinload(Requirement.test_cases).selectinload(TestCase.versions),
        )
    ).all()
    return [r for r in requirements if r.approval is not None]


def existing_links(db: Session, repo: Repository) -> dict[tuple[int, str, str], TraceLink]:
    links = db.scalars(select(TraceLink).where(TraceLink.repository_id == repo.id)).all()
    return {(link.requirement_id, link.target_type, link.target_key): link for link in links}


def recover_links(db: Session, project_id: int, repo: Repository, llm: LLMClient | None) -> RecoverResult:
    """Explicit links and AI suggestions from the stored analysis. The caller commits."""
    requirements = approved_requirements(db, project_id)
    notices: list[str] = []
    if not requirements:
        notices.append("No approved requirements yet: approve requirements to get trace links.")
        return RecoverResult(explicit_added=0, suggested_added=0, dismissed=0, unchecked=0, notices=notices)

    snapshot = latest_snapshot(db, repo)
    commits = db.scalars(select(RepoCommit).where(RepoCommit.repository_id == repo.id)).all()
    pulls = db.scalars(select(RepoPull).where(RepoPull.repository_id == repo.id)).all()
    existing = existing_links(db, repo)

    found = find_explicit({r.number: r for r in requirements}, snapshot, list(commits), list(pulls), repo.branches)
    explicit_added = add_explicit_links(db, project_id, repo, found, existing)
    db.flush()

    targets = all_targets(db, repo, snapshot)
    suggested = suggest_links(db, project_id, repo, requirements, targets, existing, llm)
    return RecoverResult(
        explicit_added=explicit_added,
        suggested_added=suggested.added,
        dismissed=suggested.dismissed,
        unchecked=suggested.unchecked,
        notices=notices + suggested.notices,
    )


def _store_commits(db: Session, repo: Repository, gh: GitHubClient, notices: list[str]) -> tuple[int, int]:
    """New commits and their files. Returns (new commits, commits whose files are still to be read)."""
    known = set(db.scalars(select(RepoCommit.sha).where(RepoCommit.repository_id == repo.id)))
    page = gh.list_new_commits(repo.default_branch, known, settings.github_max_commits)
    for c in page.commits:
        db.add(
            RepoCommit(
                repository_id=repo.id,
                sha=c.sha,
                message=c.message,
                author=c.author[:200],
                committed_at=c.committed_at,
                files=[],
                files_fetched=False,
            )
        )
    if not page.reached_known and len(page.commits) >= settings.github_max_commits:
        notices.append(f"Only the newest {settings.github_max_commits} commits were read (GITHUB_MAX_COMMITS).")
    db.flush()

    pending = db.scalars(
        select(RepoCommit)
        .where(RepoCommit.repository_id == repo.id, RepoCommit.files_fetched.is_(False))
        .order_by(RepoCommit.committed_at.desc())
    ).all()
    stopped = False
    for i, commit in enumerate(pending):
        if i >= MAX_COMMIT_FILE_REQUESTS or not gh.budget_left():
            stopped = True
            break
        commit.files = gh.commit_files(commit.sha)
        commit.files_fetched = True
    left = sum(1 for c in pending if not c.files_fetched)
    if stopped and left:
        notices.append(
            f"The changed files of {left} commits were not read yet, to stay inside GitHub's rate limit. "
            "Analyse again later to read them."
        )
    return len(page.commits), left


def _store_pulls(db: Session, repo: Repository, gh: GitHubClient) -> int:
    pulls = gh.list_pulls_since(repo.pulls_synced_at)
    stored = {
        p.number: p for p in db.scalars(select(RepoPull).where(RepoPull.repository_id == repo.id)).all()
    }
    for info in pulls:
        row = stored.get(info.number)
        if row is None:
            row = RepoPull(repository_id=repo.id, number=info.number)
            db.add(row)
            stored[info.number] = row
        row.title, row.body, row.state = info.title, info.body or "", info.state
        row.merged, row.merged_at, row.head_ref = info.merged, info.merged_at, info.head_ref[:255]
        row.author, row.updated_at_github = info.author[:200], info.updated_at
    times = [p.updated_at for p in pulls if p.updated_at is not None]
    if times:
        newest = max(times)
        if repo.pulls_synced_at is None or newest > _aware(repo.pulls_synced_at):
            repo.pulls_synced_at = newest
    return len(pulls)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _store_snapshot(db: Session, repo: Repository, gh: GitHubClient, head: str) -> RepoSnapshot:
    snapshot = db.scalar(
        select(RepoSnapshot).where(RepoSnapshot.repository_id == repo.id, RepoSnapshot.commit_sha == head)
    )
    if snapshot is not None:
        return snapshot
    data = gh.download_tarball(head, settings.github_max_repo_mb * 1024 * 1024)
    try:
        parsed = parse_tarball(data)
    except (tarfile.TarError, EOFError, OSError):
        raise ConnectorError("GitHub returned a repository archive that could not be read. Try again.") from None
    snapshot = RepoSnapshot(
        repository_id=repo.id,
        commit_sha=head,
        file_count=len(parsed.files),
        python_files=parsed.python_files,
        test_functions=parsed.test_functions,
        truncated=parsed.truncated,
    )
    db.add(snapshot)
    db.flush()
    db.add_all(
        RepoFile(
            snapshot_id=snapshot.id,
            path=f.path[:1000],
            kind=f.kind,
            symbols=f.symbols,
            mentions=f.mentions,
            docstring=f.docstring,
            parse_error=f.parse_error,
        )
        for f in parsed.files
    )
    db.flush()
    return snapshot


def analyze(db: Session, project_id: int, repo: Repository, gh: GitHubClient, llm: LLMClient | None) -> AnalysisResult:
    """Fetch what is new, store it, recover links and remember the result. Commits."""
    notices: list[str] = []
    head = gh.branch_head(repo.default_branch)
    head_changed = head != repo.head_sha
    new_commits, files_pending = _store_commits(db, repo, gh, notices)
    pulls_updated = _store_pulls(db, repo, gh)
    repo.branches = [{"name": name, "sha": sha} for name, sha in gh.list_branches()]
    snapshot = _store_snapshot(db, repo, gh, head)
    repo.head_sha = head
    db.flush()

    if not head_changed and not new_commits and not pulls_updated:
        notices.insert(0, "Nothing new on GitHub since the last analysis.")
    recovered = recover_links(db, project_id, repo, llm)
    result = AnalysisResult(
        head_sha=head,
        head_changed=head_changed,
        new_commits=new_commits,
        commit_files_pending=files_pending,
        pulls_updated=pulls_updated,
        files=snapshot.file_count,
        python_files=snapshot.python_files,
        test_functions=snapshot.test_functions,
        explicit_added=recovered.explicit_added,
        suggested_added=recovered.suggested_added,
        dismissed=recovered.dismissed,
        unchecked=recovered.unchecked,
        rate_limit_remaining=gh.remaining,
        github_requests=gh.requests,
        notices=notices + recovered.notices,
    )
    repo.last_analyzed_at = datetime.now(UTC)
    repo.last_result = result.model_dump()
    db.commit()
    return result
