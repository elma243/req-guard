"""The project's GitHub repository and its analysis (Step 10). Owner only.

GITHUB_TOKEN stays in the backend: responses only say whether it is set.
"""

import httpx
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, OptionalLLM, OwnedProject
from app.code.analyze import analyze
from app.code.github import GitHubClient, parse_repo_name
from app.connectors.http import TIMEOUT, ConnectorError
from app.core.config import settings
from app.models import Project, RepoCommit, RepoPull, Repository
from app.schemas.trace import AnalysisResult, CommitOut, PullOut, RepoConnect, RepoState, SnapshotFile, SnapshotOut
from app.trace.matrix import repo_out
from app.trace.targets import github_url, latest_snapshot

router = APIRouter(prefix="/projects/{project_id}", tags=["repository"])

RECENT = 30


def token_configured() -> bool:
    return bool((settings.github_token or "").strip())


_http: httpx.Client | None = None


def github_client(owner: str, name: str) -> GitHubClient:
    """A client for one repository. They share one HTTP connection pool (thread-safe), made on first use."""
    global _http
    if _http is None:
        _http = httpx.Client(timeout=TIMEOUT)
    return GitHubClient(owner, name, token=settings.github_token, client=_http)


def project_repo(db: Session, project: Project) -> Repository | None:
    return db.scalar(select(Repository).where(Repository.project_id == project.id))


def require_repo(db: Session, project: Project) -> Repository:
    repo = project_repo(db, project)
    if repo is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect a GitHub repository on the project page first.")
    return repo


def _state(repo: Repository | None) -> RepoState:
    return RepoState(repo=repo_out(repo) if repo else None, token_configured=token_configured())


def _bad_gateway(exc: ConnectorError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.get("/repo")
def get_repo(project: OwnedProject, db: DbSession) -> RepoState:
    return _state(project_repo(db, project))


@router.put("/repo")
def connect_repo(body: RepoConnect, project: OwnedProject, db: DbSession) -> RepoState:
    """Link a repository (or replace the linked one). Checks on GitHub that it exists and can be read."""
    try:
        owner, name = parse_repo_name(body.repository)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        with github_client(owner, name) as gh:
            info = gh.get_repo()
    except ConnectorError as exc:
        raise _bad_gateway(exc) from None

    repo = project_repo(db, project)
    if repo is not None and (repo.owner.lower(), repo.name.lower()) != (info.owner.lower(), info.name.lower()):
        # Another repository: its snapshots, commits and links belong to the old one
        db.delete(repo)
        db.flush()
        repo = None
    if repo is None:
        repo = Repository(project_id=project.id, owner=info.owner, name=info.name, default_branch=info.default_branch)
        db.add(repo)
    repo.owner, repo.name = info.owner, info.name
    repo.default_branch, repo.private = info.default_branch, info.private
    db.commit()
    return _state(repo)


@router.delete("/repo", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_repo(project: OwnedProject, db: DbSession) -> Response:
    """Removes the repository with its snapshots, commits, pull requests and trace links."""
    repo = project_repo(db, project)
    if repo is not None:
        db.delete(repo)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/repo/analyze")
def analyze_repo(project: OwnedProject, db: DbSession, llm: OptionalLLM) -> AnalysisResult:
    repo = require_repo(db, project)
    try:
        with github_client(repo.owner, repo.name) as gh:
            return analyze(db, project.id, repo, gh, llm)
    except ConnectorError as exc:
        db.rollback()
        raise _bad_gateway(exc) from None


@router.get("/repo/snapshot")
def get_snapshot(project: OwnedProject, db: DbSession) -> SnapshotOut:
    repo = require_repo(db, project)
    snapshot = latest_snapshot(db, repo)
    commits = db.scalars(
        select(RepoCommit)
        .where(RepoCommit.repository_id == repo.id)
        .order_by(RepoCommit.committed_at.desc(), RepoCommit.id)
        .limit(RECENT)
    ).all()
    pulls = db.scalars(
        select(RepoPull).where(RepoPull.repository_id == repo.id).order_by(RepoPull.number.desc()).limit(RECENT)
    ).all()
    python = [
        SnapshotFile(
            path=f.path,
            kind=f.kind,
            tests=sum(1 for s in f.symbols if s["kind"] == "test"),
            url=github_url(repo, "file", f.path),
            parse_error=f.parse_error,
        )
        for f in (snapshot.files if snapshot else [])
        if f.kind != "other"
    ]
    return SnapshotOut(
        commit_sha=snapshot.commit_sha if snapshot else None,
        file_count=snapshot.file_count if snapshot else 0,
        python_files=snapshot.python_files if snapshot else 0,
        test_functions=snapshot.test_functions if snapshot else 0,
        truncated=snapshot.truncated if snapshot else False,
        python=python,
        commits=[
            CommitOut(
                sha=c.sha,
                title=c.title,
                author=c.author,
                committed_at=c.committed_at,
                files=c.files,
                files_fetched=c.files_fetched,
                url=github_url(repo, "commit", c.sha),
            )
            for c in commits
        ],
        pulls=[
            PullOut(
                number=p.number,
                title=p.title,
                state=p.state,
                merged=p.merged,
                author=p.author,
                head_ref=p.head_ref,
                url=github_url(repo, "pr", str(p.number)),
            )
            for p in pulls
        ],
    )
