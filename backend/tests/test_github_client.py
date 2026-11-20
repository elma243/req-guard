"""The GitHub REST client (Step 10), against a fake api.github.com (respx). No network."""

from datetime import timedelta

import httpx
import pytest
import respx

from app.code.github import GitHubClient, parse_repo_name
from app.connectors.http import AudioTooLargeError, ConnectorError
from tests.trace_helpers import API, CODELOAD, T0, FakeGitHub

TOKEN = "ghp_secret_token_0123456789"


@pytest.fixture
def github():
    with respx.mock(assert_all_called=False) as router:
        yield FakeGitHub(router)


# One HTTP client for the module: creating one per test costs about a second (SSL setup); respx still mocks it
_HTTP = httpx.Client()


def client(token: str | None = TOKEN) -> GitHubClient:
    return GitHubClient("acme", "shop", token=token, client=_HTTP)


@pytest.mark.parametrize(
    "text",
    [
        "acme/shop",
        " acme/shop ",
        "https://github.com/acme/shop",
        "https://github.com/acme/shop.git",
        "github.com/acme/shop/",
        "https://www.github.com/acme/shop/tree/main/app",
    ],
)
def test_repo_name_forms(text):
    assert parse_repo_name(text) == ("acme", "shop")


@pytest.mark.parametrize("text", ["shop", "acme/", "/shop", "https://gitlab.com/acme/shop", "acme shop", "-a/b"])
def test_bad_repo_names(text):
    with pytest.raises(ValueError):
        parse_repo_name(text)


def test_repo_info_and_head(github):
    with client() as gh:
        info = gh.get_repo()
        assert (info.owner, info.name, info.default_branch, info.private) == ("acme", "shop", "main", False)
        assert gh.branch_head("main") == github.c3
        assert gh.remaining == 4997
    request = github.repo_route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_no_authorization_header_without_a_token(github):
    with client(token=None) as gh:
        gh.get_repo()
    assert "Authorization" not in github.repo_route.calls.last.request.headers


def test_commits_stop_at_a_known_sha(github):
    with client() as gh:
        page = gh.list_new_commits("main", known={github.c2}, limit=200)
    assert [c.sha for c in page.commits] == [github.c3]
    assert page.reached_known
    first = page.commits[0]
    assert first.message == "Add password reset email (REQ-002)"
    assert first.author == "rahim"
    assert first.committed_at == T0 + timedelta(hours=3)


def test_commits_stop_at_the_limit(github):
    with client() as gh:
        page = gh.list_new_commits("main", known=set(), limit=2)
        assert [c.sha for c in page.commits] == [github.c3, github.c2]
        assert not page.reached_known
        assert len(gh.list_new_commits("main", known=set(), limit=200).commits) == 3


def test_pages_follow_the_link_header(github):
    with client() as gh:
        pages = list(gh._pages(f"{API.removeprefix('https://api.github.com')}/commits", {"sha": "main", "per_page": 1}))
    assert [p[0]["sha"] for p in pages] == [github.c3, github.c2, github.c1]
    assert len(github.commits_route.calls) == 3
    assert "page=2" in str(github.commits_route.calls[1].request.url)


def test_commit_files(github):
    with client() as gh:
        assert gh.commit_files(github.c3) == ["app/auth.py", "tests/test_auth.py"]


def test_pulls_since_the_cursor(github):
    with client() as gh:
        everything = gh.list_pulls_since(None)
        assert [p.number for p in everything] == [2, 1]
        merged = everything[0]
        assert merged.merged and merged.head_ref == "feature/REQ-002-reset" and merged.author == "karim"
        assert not everything[1].merged
        newer = gh.list_pulls_since(T0 + timedelta(hours=1))
        assert [p.number for p in newer] == [2]


def test_branches(github):
    with client() as gh:
        assert gh.list_branches() == github.branches


def test_rate_limit_gives_the_reset_time(github):
    github.rate_limited = True
    with client(token=None) as gh, pytest.raises(ConnectorError) as exc:
        gh.get_repo()
    message = str(exc.value)
    assert "rate limit" in message and f"{T0:%H:%M} UTC" in message
    assert "GITHUB_TOKEN" in message


def test_budget_tracks_remaining_requests(github):
    github.remaining = 52
    with client() as gh:
        assert gh.budget_left()
        gh.get_repo()
        assert gh.remaining == 51 and gh.budget_left()
        gh.get_repo()
        assert gh.remaining == 50 and not gh.budget_left()


def test_not_found_names_the_repo_and_never_the_token():
    with respx.mock:
        respx.get(API).respond(404, json={"message": "Not Found"})
        with client() as gh, pytest.raises(ConnectorError) as exc:
            gh.get_repo()
    assert "acme/shop" in str(exc.value) and "GITHUB_TOKEN" in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_bad_token():
    with respx.mock:
        respx.get(API).respond(401, json={"message": "Bad credentials"})
        with client() as gh, pytest.raises(ConnectorError) as exc:
            gh.get_repo()
    assert "HTTP 401" in str(exc.value) and TOKEN not in str(exc.value)


def test_network_error_message_has_no_token_or_url():
    with respx.mock:
        respx.get(API).mock(side_effect=httpx.ConnectError("boom"))
        with client() as gh, pytest.raises(ConnectorError) as exc:
            gh.get_repo()
    assert "Could not reach GitHub" in str(exc.value)
    assert TOKEN not in str(exc.value) and "api.github.com" not in str(exc.value)


def test_tarball_follows_the_redirect_without_the_token(github):
    with client() as gh:
        data = gh.download_tarball(github.c3, max_bytes=10_000_000)
    assert data == github.tarball
    assert github.tarball_route.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Authorization" not in github.codeload_route.calls.last.request.headers


def test_tarball_size_cap(github):
    with client() as gh, pytest.raises(AudioTooLargeError) as exc:
        gh.download_tarball(github.c3, max_bytes=100)
    assert "repository archive" in str(exc.value)


def test_codeload_url_constant():
    assert CODELOAD.startswith("https://codeload.github.com/")
