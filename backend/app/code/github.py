"""GitHub REST API client for repository analysis (Step 10).

Only reads: the repository, the head of a branch, commits (with their changed files), pull requests, branches and
the tarball of one commit. Every request goes through `send()` from app/connectors/http.py (429 retries, clear
errors, no URL or token in messages).

Rate limits: GitHub allows 60 requests per hour without a token and 5000 with one. The client tracks
X-RateLimit-Remaining; `budget_left()` lets the analysis skip optional requests (commit file lists) when few are
left, and a request refused for the rate limit raises ConnectorError with the time it resets.

The token is only sent to api.github.com. The tarball request is redirected to codeload.github.com, and httpx drops
the Authorization header on a redirect to another host.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.connectors.http import MAX_PAGES, TIMEOUT, ConnectorError, download, json_body, send

SERVICE = "GitHub"
API = "https://api.github.com"
AUTH_HINT = "Check GITHUB_TOKEN in backend/.env."
# Optional requests (commit file lists) stop when fewer requests than this are left
RESERVE = 50

_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_NAME = r"[A-Za-z0-9._-]{1,100}"
_REPO = re.compile(
    rf"^(?:(?:https?://)?(?:www\.)?github\.com/)?(?P<owner>{_OWNER})/(?P<name>{_NAME}?)(?:\.git)?/?(?:[/?#].*)?$"
)


def parse_repo_name(text: str) -> tuple[str, str]:
    """(owner, name) from "owner/repo" or a github.com URL; ValueError when it is neither."""
    match = _REPO.match((text or "").strip())
    if not match:
        raise ValueError('Enter the repository as "owner/repo" or its github.com address.')
    owner, name = match.group("owner"), match.group("name")
    if name.endswith(".git"):
        name = name[:-4]
    if not name or name in (".", ".."):
        raise ValueError('Enter the repository as "owner/repo" or its github.com address.')
    return owner, name


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class RepoInfo:
    owner: str
    name: str
    default_branch: str
    private: bool


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    committed_at: datetime | None


@dataclass
class PullInfo:
    number: int
    title: str
    body: str
    state: str
    merged: bool
    merged_at: datetime | None
    head_ref: str
    author: str
    updated_at: datetime | None


@dataclass
class CommitPage:
    commits: list[CommitInfo] = field(default_factory=list)
    # True when the list reached a commit that was already stored (everything older is stored too)
    reached_known: bool = False


class GitHubClient:
    def __init__(self, owner: str, name: str, token: str | None = None, client: httpx.Client | None = None):
        self.owner = owner
        self.name = name
        self._token = (token or "").strip() or None
        self._own_client = client is None
        self.client = client or httpx.Client(timeout=TIMEOUT)
        self.remaining: int | None = None
        self.reset_at: datetime | None = None
        self.requests = 0

    # ---------- plumbing ----------

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @property
    def repo_path(self) -> str:
        return f"/repos/{self.owner}/{self.name}"

    def budget_left(self, reserve: int = RESERVE) -> bool:
        """True while more than `reserve` requests are left (or the limit is not known yet)."""
        return self.remaining is None or self.remaining > reserve

    def _track(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and remaining.isdigit():
            self.remaining = int(remaining)
        if reset is not None and reset.isdigit():
            self.reset_at = datetime.fromtimestamp(int(reset), tz=UTC)

    def _rate_limited(self) -> ConnectorError:
        when = f"; it resets at {self.reset_at:%H:%M} UTC" if self.reset_at else ""
        hint = "" if self._token else " Set GITHUB_TOKEN in backend/.env for 5000 requests per hour."
        return ConnectorError(f"GitHub rate limit reached{when}.{hint}")

    def _get(self, url: str, params: dict | None = None, not_found: str | None = None) -> httpx.Response:
        if url.startswith("/"):
            url = API + url
        response = send(
            self.client,
            "GET",
            url,
            service=SERVICE,
            auth_hint=AUTH_HINT,
            allow_status=(403, 404),
            headers=self.headers,
            params=params,
        )
        self.requests += 1
        self._track(response)
        if response.status_code == 403:
            if response.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in response.text.lower():
                raise self._rate_limited()
            raise ConnectorError(f"GitHub denied access (HTTP 403). Check the permissions of the token. {AUTH_HINT}")
        if response.status_code == 404:
            raise ConnectorError(not_found or "GitHub could not find the requested item (HTTP 404).")
        return response

    def _pages(self, path: str, params: dict, max_pages: int = MAX_PAGES):
        """Each page's JSON list, following the Link: next header."""
        url: str | None = path
        page_params: dict | None = params
        for _ in range(max_pages):
            response = self._get(url, page_params)
            data = json_body(response, SERVICE)
            if not isinstance(data, list):
                raise ConnectorError("GitHub returned an unexpected response.")
            yield data
            url = (response.links.get("next") or {}).get("url")
            page_params = None  # the next URL carries them
            if not url:
                return

    # ---------- reads ----------

    def get_repo(self) -> RepoInfo:
        not_found = (
            f"GitHub could not find {self.owner}/{self.name}. Check the name; a private repository needs "
            "GITHUB_TOKEN with read access to it."
        )
        data = json_body(self._get(self.repo_path, not_found=not_found), SERVICE)
        full = str(data.get("full_name") or f"{self.owner}/{self.name}")
        owner, _, name = full.partition("/")
        return RepoInfo(
            owner=owner or self.owner,
            name=name or self.name,
            default_branch=str(data.get("default_branch") or "main"),
            private=bool(data.get("private")),
        )

    def branch_head(self, branch: str) -> str:
        data = json_body(
            self._get(f"{self.repo_path}/branches/{branch}", not_found=f"GitHub could not find the branch {branch}."),
            SERVICE,
        )
        sha = (data.get("commit") or {}).get("sha")
        if not sha:
            raise ConnectorError("GitHub returned no head commit for the branch.")
        return str(sha)

    def list_new_commits(self, branch: str, known: set[str], limit: int) -> CommitPage:
        """Commits of the branch, newest first, until a known SHA or `limit` commits."""
        result = CommitPage()
        params = {"sha": branch, "per_page": min(100, max(1, limit))}
        for page in self._pages(f"{self.repo_path}/commits", params):
            for item in page:
                sha = str(item.get("sha") or "")
                if not sha:
                    continue
                if sha in known:
                    result.reached_known = True
                    return result
                commit = item.get("commit") or {}
                author = commit.get("author") or {}
                login = (item.get("author") or {}).get("login")
                result.commits.append(
                    CommitInfo(
                        sha=sha,
                        message=str(commit.get("message") or ""),
                        author=str(login or author.get("name") or ""),
                        committed_at=parse_time(author.get("date")),
                    )
                )
                if len(result.commits) >= limit:
                    return result
        return result

    def commit_files(self, sha: str) -> list[str]:
        """Paths one commit changed (the first 300, GitHub's first page)."""
        data = json_body(self._get(f"{self.repo_path}/commits/{sha}"), SERVICE)
        return [str(f.get("filename")) for f in (data.get("files") or []) if f.get("filename")]

    def list_pulls_since(self, cursor: datetime | None) -> list[PullInfo]:
        """Pull requests (open and closed), most recently updated first, until one not updated after `cursor`."""
        pulls: list[PullInfo] = []
        if cursor is not None and cursor.tzinfo is None:
            cursor = cursor.replace(tzinfo=UTC)  # SQLite gives naive UTC times
        params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
        for page in self._pages(f"{self.repo_path}/pulls", params, max_pages=10):
            for item in page:
                updated = parse_time(item.get("updated_at"))
                if cursor is not None and updated is not None and updated <= cursor:
                    return pulls
                merged_at = parse_time(item.get("merged_at"))
                pulls.append(
                    PullInfo(
                        number=int(item.get("number") or 0),
                        title=str(item.get("title") or ""),
                        body=str(item.get("body") or ""),
                        state=str(item.get("state") or "open"),
                        merged=merged_at is not None,
                        merged_at=merged_at,
                        head_ref=str((item.get("head") or {}).get("ref") or ""),
                        author=str((item.get("user") or {}).get("login") or ""),
                        updated_at=updated,
                    )
                )
        return pulls

    def list_branches(self) -> list[tuple[str, str]]:
        """(name, head sha) of each branch (at most 300)."""
        branches: list[tuple[str, str]] = []
        for page in self._pages(f"{self.repo_path}/branches", {"per_page": 100}, max_pages=3):
            for item in page:
                name, sha = item.get("name"), (item.get("commit") or {}).get("sha")
                if name and sha:
                    branches.append((str(name), str(sha)))
        return branches

    def download_tarball(self, sha: str, max_bytes: int) -> bytes:
        self.requests += 1
        return download(
            self.client,
            f"{API}{self.repo_path}/tarball/{sha}",
            service=SERVICE,
            auth_hint=AUTH_HINT,
            max_bytes=max_bytes,
            what="repository archive",
            headers=self.headers,
        )
