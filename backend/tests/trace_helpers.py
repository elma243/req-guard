"""Shared setup for the Step 10 tests: a fake GitHub (respx), approved requirements, embeddings by topic and short
API calls for the repository and trace links. Nothing reaches the network."""

import hashlib
import zlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import respx

from tests.case_helpers import call, seed_approved
from tests.fake_models import unit
from tests.repo_fixture import sample_tarball

API = "https://api.github.com/repos/acme/shop"
CODELOAD = "https://codeload.github.com/acme/shop/legacy.tar.gz"

SIGN_IN = "The customer shall sign in with an email address."  # REQ-001
RESET = "The customer shall reset the password by email."  # REQ-002
SALES = "The admin shall export the monthly sales report as CSV."  # REQ-003


def sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def commit_json(sha_: str, message: str, when: datetime, login: str = "rahim") -> dict:
    return {
        "sha": sha_,
        "commit": {"message": message, "author": {"name": login.title(), "date": when.isoformat()}},
        "author": {"login": login},
    }


def pull_json(number: int, title: str, body: str, head_ref: str, updated: datetime, merged: bool = False) -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": "closed" if merged else "open",
        "merged_at": updated.isoformat() if merged else None,
        "head": {"ref": head_ref},
        "user": {"login": "karim"},
        "updated_at": updated.isoformat(),
    }


T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


class FakeGitHub:
    """The acme/shop repository on a fake api.github.com. Change the attributes, then analyse again."""

    def __init__(self, router: respx.Router):
        self.private = False
        self.c1, self.c2, self.c3 = sha("c1"), sha("c2"), sha("c3")
        self.commits = [  # newest first
            commit_json(self.c3, "Add password reset email (REQ-002)", T0 + timedelta(hours=3)),
            commit_json(self.c2, "Export sales report as CSV", T0 + timedelta(hours=2)),
            commit_json(self.c1, "Initial commit", T0 + timedelta(hours=1)),
        ]
        self.files = {self.c3: ["app/auth.py", "tests/test_auth.py"], self.c2: ["app/reports.py"], self.c1: ["README.md"]}
        self.pulls = [
            pull_json(2, "Password reset", "Sends a reset link.", "feature/REQ-002-reset", T0 + timedelta(hours=4), True),
            pull_json(1, "Sales report export", "Exports the monthly sales report as a CSV file.", "reports", T0),
        ]
        self.head = self.c3
        self.branches = [("main", self.c3), ("feature/REQ-002-reset", self.c3), ("feature/REQ-001-sign-in", self.c1)]
        self.tarball = sample_tarball()
        self.remaining = 4999
        self.rate_limited = False

        r = router
        self.repo_route = r.get(API).mock(side_effect=self._repo)
        self.head_route = r.get(f"{API}/branches/main").mock(side_effect=self._head)
        self.branches_route = r.get(f"{API}/branches").mock(side_effect=self._branches)
        self.commits_route = r.get(f"{API}/commits").mock(side_effect=self._commits)
        self.commit_route = r.get(url__regex=rf"^{API}/commits/[0-9a-f]{{40}}$").mock(side_effect=self._commit)
        self.pulls_route = r.get(f"{API}/pulls").mock(side_effect=self._pulls)
        self.tarball_route = r.get(url__regex=rf"^{API}/tarball/[0-9a-f]{{40}}$").mock(side_effect=self._tarball)
        self.codeload_route = r.get(CODELOAD).mock(side_effect=self._codeload)

    # ---------- responses ----------

    def _response(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if self.rate_limited:
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(T0.timestamp()))},
            )
        self.remaining -= 1
        headers = {"X-RateLimit-Remaining": str(self.remaining), "X-RateLimit-Reset": str(int(T0.timestamp()))}
        headers.update(kwargs.pop("headers", {}))
        return httpx.Response(kwargs.pop("status", 200), headers=headers, **kwargs)

    def _repo(self, request):
        return self._response(
            request, json={"full_name": "acme/shop", "default_branch": "main", "private": self.private}
        )

    def _head(self, request):
        return self._response(request, json={"name": "main", "commit": {"sha": self.head}})

    def _branches(self, request):
        return self._response(request, json=[{"name": n, "commit": {"sha": s}} for n, s in self.branches])

    def _paged(self, request, items: list) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        per_page = int(query.get("per_page", ["30"])[0])
        page = int(query.get("page", ["1"])[0])
        chunk = items[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(items):
            base = str(request.url).split("?")[0]
            rest = "&".join(f"{k}={v[0]}" for k, v in query.items() if k != "page")
            headers["Link"] = f'<{base}?{rest}&page={page + 1}>; rel="next"'
        return self._response(request, json=chunk, headers=headers)

    def _commits(self, request):
        return self._paged(request, self.commits)

    def _commit(self, request):
        sha_ = str(request.url).rsplit("/", 1)[1]
        return self._response(request, json={"sha": sha_, "files": [{"filename": f} for f in self.files.get(sha_, [])]})

    def _pulls(self, request):
        ordered = sorted(self.pulls, key=lambda p: p["updated_at"], reverse=True)
        return self._paged(request, ordered)

    def _tarball(self, request):
        return self._response(request, status=302, headers={"Location": CODELOAD})

    def _codeload(self, request):
        return httpx.Response(200, content=self.tarball, headers={"Content-Type": "application/x-gzip"})

    # ---------- changes ----------

    def push(self, message: str, files: list[str], extra: dict[str, str] | None = None) -> str:
        """A new commit on main; `extra` changes files of the tarball."""
        new = sha(message)
        when = T0 + timedelta(days=1, minutes=len(self.commits))
        self.commits.insert(0, commit_json(new, message, when))
        self.files[new] = files
        self.head = new
        self.branches[0] = ("main", new)
        if extra:
            self.tarball = sample_tarball(extra=extra)
        return new


# ---------- embeddings by topic ----------

TOPICS = {"sales": unit(1), "password": unit(2), "sign in": unit(3)}


def embed_by_topic(fake_models, topics: dict[str, list[float]] | None = None) -> None:
    """A text gets the vector of the first topic word it contains; texts without one are far from everything."""
    topics = TOPICS if topics is None else topics
    embedder = fake_models.embedder

    def encode(texts):
        embedder.calls.append(list(texts))
        vectors = []
        for text in texts:
            low = text.lower()
            vector = next((v for word, v in topics.items() if word in low), None)
            vectors.append(vector or unit(100 + zlib.crc32(low.encode()) % 250))
        return vectors

    embedder.encode = encode


# ---------- API ----------


def requirements(client, project, db_session_factory) -> list[int]:
    """REQ-001 sign in, REQ-002 password reset, REQ-003 sales report: all approved."""
    return [seed_approved(client, project, db_session_factory, s) for s in (SIGN_IN, RESET, SALES)]


def connect(client, project, name: str = "acme/shop") -> dict:
    res = call(client, project, "PUT", "/repo", {"repository": name})
    assert res.status_code == 200, res.text
    return res.json()


def analyze(client, project) -> dict:
    res = call(client, project, "POST", "/repo/analyze")
    assert res.status_code == 200, res.text
    return res.json()


def matrix(client, project, query: str = "") -> dict:
    res = call(client, project, "GET", f"/trace{query}")
    assert res.status_code == 200, res.text
    return res.json()


def rows(client, project) -> dict[str, dict]:
    return {row["requirement"]["code"]: row for row in matrix(client, project)["rows"]}


def links(row: dict) -> dict[str, dict]:
    """A row's links by "target_type:target_key"."""
    return {f"{link['target_type']}:{link['target_key']}": link for link in row["implements"] + row["verifies"]}


def decide(client, project, link_id: int, action: str) -> dict:
    res = call(client, project, "POST", f"/trace/links/{link_id}/{action}")
    assert res.status_code == 200, res.text
    return res.json()


# ---------- Step 8B criteria and test cases ----------


def seed_criteria(db_session_factory, rid: int, items: list[tuple[str, list[str]]], status: str = "accepted") -> None:
    """Acceptance criteria (text, [test case titles]) for a requirement, straight in the database."""
    from app.models import AcceptanceCriterion, AcceptanceCriterionVersion, Requirement, TestCase, TestCaseVersion

    with db_session_factory() as db:
        requirement = db.get(Requirement, rid)
        version = requirement.current.version
        for text, titles in items:
            requirement.criterion_counter += 1
            criterion = AcceptanceCriterion(
                requirement_id=rid,
                number=requirement.criterion_counter,
                origin="ai",
                status=status,
                requirement_version=version,
                versions=[AcceptanceCriterionVersion(version=1, text=text, origin="ai")],
            )
            db.add(criterion)
            db.flush()
            for title in titles:
                requirement.test_case_counter += 1
                db.add(
                    TestCase(
                        requirement_id=rid,
                        number=requirement.test_case_counter,
                        origin="ai",
                        status="accepted",
                        requirement_version=version,
                        versions=[
                            TestCaseVersion(
                                version=1,
                                title=title,
                                type="positive",
                                given="a customer",
                                when="they act",
                                then="it works",
                                criterion_id=criterion.id,
                                origin="ai",
                            )
                        ],
                    )
                )
        db.commit()
