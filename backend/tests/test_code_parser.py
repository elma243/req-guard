"""The repository tarball parser and the REQ / TC ID matcher (Step 10). Pure: no database, no network."""

import pytest

from app.code.parser import file_kind, parse_python, parse_tarball
from app.trace.ids import find_req_numbers, find_tc_numbers
from tests.repo_fixture import sample_tarball


@pytest.fixture(scope="module")
def repo():
    parsed = parse_tarball(sample_tarball())
    return parsed, {f.path: f for f in parsed.files}


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("Fix REQ-004 login", [4]),
        ("req_4 and REQ-0004", [4]),
        ("REQ12 then REQ-12 again", [12]),
        ("def test_req_004_login(): pass", [4]),
        ("feature/REQ-7-reset", [7]),
        ("PREREQ-4 is not an ID", []),
        ("REQ- without a number", []),
        ("REQUIRED field", []),
        ("REQ-0 is not a requirement", []),
        ("REQ-1, REQ-2", [1, 2]),
        (None, []),
    ],
)
def test_requirement_ids(text, numbers):
    assert find_req_numbers(text) == numbers


def test_test_case_ids():
    assert find_tc_numbers("TC-1: a valid login works; see tc_3") == [1, 3]
    assert find_tc_numbers("ETC-1") == []


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("app/auth.py", "code"),
        ("tests/test_auth.py", "test"),
        ("app/auth_test.py", "test"),
        ("test_main.py", "test"),
        ("src/tests/helpers.py", "test"),
        ("README.md", "other"),
        ("tests/data.json", "other"),
    ],
)
def test_file_kind(path, kind):
    assert file_kind(path) == kind


def test_tree_keeps_every_file_and_skips_vendored_folders(repo):
    parsed, files = repo
    assert set(files) == {
        "README.md",
        "app/__init__.py",
        "app/auth.py",
        "app/broken.py",
        "app/reports.py",
        "tests/__init__.py",
        "tests/test_auth.py",
    }
    assert parsed.truncated  # site-packages/ was left out
    assert parsed.python_files == 6
    assert files["README.md"].kind == "other" and files["README.md"].symbols == []


def test_symbols_with_line_ranges_and_docstrings(repo):
    _, files = repo
    auth = files["app/auth.py"]
    assert auth.docstring == "Login and password reset for shop customers."
    by_name = {s["qualname"]: s for s in auth.symbols}
    assert set(by_name) == {"login", "reset_password", "Session", "Session.close"}
    assert by_name["login"]["kind"] == "function"
    assert (by_name["login"]["line_start"], by_name["login"]["line_end"]) == (4, 6)
    assert by_name["reset_password"]["docstring"] == "Send a password reset link by email (REQ-002)."
    assert by_name["Session"]["kind"] == "class"


def test_tests_follow_pytest_naming(repo):
    _, files = repo
    kinds = {s["qualname"]: s["kind"] for s in files["tests/test_auth.py"].symbols}
    assert kinds == {
        "test_req_001_login_with_valid_password": "test",
        "test_valid_request_works": "test",
        "TestReset": "class",
        "TestReset.test_reset_link_expires": "test",
        "TestReset.helper": "function",
        "helper_not_a_test": "function",
    }
    # A function named test* in a code file is not a test
    code = parse_python("app/x.py", "def test_like():\n    pass\n")
    assert code.symbols[0]["kind"] == "function"


def test_mentions_in_comments_docstrings_and_names(repo):
    _, files = repo
    assert files["app/auth.py"].mentions == [
        {"number": 1, "line": 5, "where": "comment", "symbol": "login"},
        {"number": 2, "line": 9, "where": "docstring", "symbol": "reset_password"},
    ]
    assert files["tests/test_auth.py"].mentions == [
        {"number": 1, "line": 6, "where": "name", "symbol": "test_req_001_login_with_valid_password"},
        {"number": 2, "line": 17, "where": "comment", "symbol": "TestReset.test_reset_link_expires"},
    ]


def test_syntax_error_keeps_the_file_and_its_comments(repo):
    _, files = repo
    broken = files["app/broken.py"]
    assert broken.parse_error.startswith("line 2")
    assert broken.symbols == []
    assert broken.mentions == [{"number": 3, "line": 1, "where": "comment", "symbol": None}]


def test_module_docstring_mention_and_comment_outside_functions():
    parsed = parse_python("tests/test_x.py", '"""Checks for REQ-9."""\n# REQ-10 setup\nX = 1\n')
    assert parsed.mentions == [
        {"number": 9, "line": 1, "where": "docstring", "symbol": None},
        {"number": 10, "line": 2, "where": "comment", "symbol": None},
    ]


def test_bangla_text_and_bad_bytes_do_not_break_parsing():
    parsed = parse_tarball(sample_tarball(extra={"app/bn.py": '"""লগইন (REQ-5)"""\n# পাসওয়ার্ড\n'}))
    bn = next(f for f in parsed.files if f.path == "app/bn.py")
    assert bn.docstring == "লগইন (REQ-5)"
    assert [m["number"] for m in bn.mentions] == [5]
