"""Reads a repository tarball (GitHub's archive of one commit) without writing anything to disk (Step 10).

Python files are parsed with `ast` (functions, classes, tests, docstrings, line ranges) and `tokenize` (comments);
every REQ-number they mention is recorded with its line and the function it is in. Other files only keep their
path, so the file tree is complete. Pure functions: no database, no network.

Tests follow pytest's naming: a test file is test_*.py, *_test.py or a file under a tests/ folder; a test is a
function named test* at module level, or a method named test* in a class named Test*.
"""

import ast
import io
import re
import tarfile
import tokenize
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.trace.ids import find_req_numbers

# Folders that hold installed or generated code, not the project's own
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "site-packages",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}
MAX_PY_BYTES = 500_000
MAX_FILES = 20_000

_COMMENT_LINE = re.compile(r"#(.*)$")


@dataclass
class ParsedFile:
    path: str
    kind: str  # code | test | other
    symbols: list[dict] = field(default_factory=list)
    mentions: list[dict] = field(default_factory=list)
    docstring: str | None = None
    parse_error: str | None = None


@dataclass
class ParsedRepo:
    files: list[ParsedFile] = field(default_factory=list)
    # Some files were left out (vendored folders, too large, too many files)
    truncated: bool = False

    @property
    def python_files(self) -> int:
        return sum(1 for f in self.files if f.path.endswith(".py"))

    @property
    def test_functions(self) -> int:
        return sum(1 for f in self.files for s in f.symbols if s["kind"] == "test")


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    if p.suffix != ".py":
        return False
    return p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts[:-1]


def file_kind(path: str) -> str:
    if not path.endswith(".py"):
        return "other"
    return "test" if is_test_path(path) else "code"


def first_line(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()[:300]
    return None


def _symbols(tree: ast.Module, test_file: bool) -> list[dict]:
    """Top-level functions and classes, and the methods of top-level classes (nested functions are skipped)."""
    symbols: list[dict] = []

    def add(node: ast.AST, kind: str, qualname: str) -> None:
        symbols.append(
            {
                "kind": kind,
                "name": node.name,
                "qualname": qualname,
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno) or node.lineno,
                "docstring": ast.get_docstring(node),
            }
        )

    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, functions):
            add(node, "test" if test_file and node.name.startswith("test") else "function", node.name)
        elif isinstance(node, ast.ClassDef):
            add(node, "class", node.name)
            test_class = test_file and node.name.startswith("Test")
            for child in node.body:
                if isinstance(child, functions):
                    kind = "test" if test_class and child.name.startswith("test") else "function"
                    add(child, kind, f"{node.name}.{child.name}")
    return symbols


def _comments(source: str) -> list[tuple[int, str]]:
    """(line, text) of each comment; a file tokenize cannot read is scanned line by line instead."""
    try:
        return [
            (tok.start[0], tok.string.lstrip("#").strip())
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, SyntaxError):
        found = []
        for number, line in enumerate(source.splitlines(), start=1):
            match = _COMMENT_LINE.search(line)
            if match:
                found.append((number, match.group(1).strip()))
        return found


def enclosing_symbol(symbols: list[dict], line: int) -> dict | None:
    """The innermost function, test or class whose lines include `line`."""
    containing = [s for s in symbols if s["line_start"] <= line <= s["line_end"]]
    return min(containing, key=lambda s: s["line_end"] - s["line_start"], default=None)


def parse_python(path: str, source: str) -> ParsedFile:
    kind = file_kind(path)
    parsed = ParsedFile(path=path, kind=kind)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None)
        parsed.parse_error = f"line {line}: {getattr(exc, 'msg', str(exc))}" if line else str(exc)[:300]
        tree = None

    mentions: list[dict] = []

    def mention(text: str | None, line: int, where: str, symbol: str | None) -> None:
        for number in find_req_numbers(text):
            item = {"number": number, "line": line, "where": where, "symbol": symbol}
            if item not in mentions:
                mentions.append(item)

    if tree is not None:
        parsed.symbols = _symbols(tree, kind == "test")
        module_doc = ast.get_docstring(tree)
        parsed.docstring = first_line(module_doc)
        mention(module_doc, 1, "docstring", None)
        for symbol in parsed.symbols:
            mention(symbol["name"], symbol["line_start"], "name", symbol["qualname"])
            mention(symbol["docstring"], symbol["line_start"], "docstring", symbol["qualname"])
    for line, text in _comments(source):
        inside = enclosing_symbol(parsed.symbols, line)
        mention(text, line, "comment", inside["qualname"] if inside else None)
    parsed.mentions = sorted(mentions, key=lambda m: (m["line"], m["number"]))
    return parsed


def _skipped(parts: tuple[str, ...]) -> bool:
    return any(part in SKIP_DIRS or part.endswith(".egg-info") for part in parts[:-1])


def parse_tarball(data: bytes) -> ParsedRepo:
    """Every file of the archive (the top folder GitHub adds is removed), Python files parsed."""
    repo = ParsedRepo()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts[1:]  # "owner-repo-sha/app/x.py" -> ("app", "x.py")
            if not parts or any(p in ("..", "") for p in parts) or member.name.startswith("/"):
                continue
            if _skipped(parts):
                repo.truncated = True
                continue
            if len(repo.files) >= MAX_FILES:
                repo.truncated = True
                break
            path = "/".join(parts)
            if not path.endswith(".py"):
                repo.files.append(ParsedFile(path=path, kind="other"))
                continue
            if member.size > MAX_PY_BYTES:
                repo.truncated = True
                repo.files.append(ParsedFile(path=path, kind=file_kind(path), parse_error="File too large to read"))
                continue
            handle = archive.extractfile(member)
            source = handle.read().decode("utf-8", errors="replace") if handle else ""
            repo.files.append(parse_python(path, source))
    repo.files.sort(key=lambda f: f.path)
    return repo
