"""The sample repository in tests/fixtures/repo as GitHub's tarball of one commit, built in memory."""

import io
import tarfile
from pathlib import Path

REPO_DIR = Path(__file__).parent / "fixtures" / "repo"
PREFIX = "acme-shop-abc1234"


def sample_tarball(extra: dict[str, str] | None = None, drop: tuple[str, ...] = (), prefix: str = PREFIX) -> bytes:
    """The sample repo; `extra` adds or replaces files (path -> text) and `drop` leaves paths out."""
    files = {
        p.relative_to(REPO_DIR).as_posix(): p.read_bytes()
        for p in sorted(REPO_DIR.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
    for path in drop:
        files.pop(path, None)
    for path, text in (extra or {}).items():
        files[path] = text.encode("utf-8")
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        folder = tarfile.TarInfo(prefix)
        folder.type = tarfile.DIRTYPE
        archive.addfile(folder)
        for path, data in files.items():
            info = tarfile.TarInfo(f"{prefix}/{path}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()
