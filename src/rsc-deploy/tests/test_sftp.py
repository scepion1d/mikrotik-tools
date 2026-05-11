"""Tests for ``rsc_deploy.sftp.SftpClient``.

We don't talk to a real router. A small fake stands in for
``paramiko.SFTPClient`` so we can assert the wrapper's behaviour:
error wrapping, ``ensure_dir`` recursion + idempotency, ``put`` / ``get``
delegation, and ``stat_size`` parsing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paramiko  # noqa: E402
import pytest  # noqa: E402

from rsc_deploy.sftp import SftpClient, SftpError  # noqa: E402


# --- fake paramiko.SFTPClient ----------------------------------------------


class _Attrs:
    def __init__(self, size: int) -> None:
        self.st_size = size


class FakeSftp:
    """Minimal in-memory stand-in for the paramiko SFTPClient surface we use."""

    def __init__(self) -> None:
        self.dirs: set[str] = set()
        self.files: dict[str, int] = {}     # path -> size
        self.calls: list[tuple[str, ...]] = []
        self.closed = False
        # Failure injection knobs (set by tests).
        self.fail_listdir: Exception | None = None
        self.fail_remove: Exception | None = None
        self.fail_stat: Exception | None = None
        self.fail_mkdir: Exception | None = None
        self.fail_put: Exception | None = None
        self.fail_get: Exception | None = None

    def listdir(self, path: str) -> list[str]:
        self.calls.append(("listdir", path))
        if self.fail_listdir:
            raise self.fail_listdir
        prefix = "" if path in (".", "") else f"{path.rstrip('/')}/"
        return [
            name[len(prefix):]
            for name in list(self.files.keys()) + list(self.dirs)
            if name.startswith(prefix) and "/" not in name[len(prefix):]
        ]

    def remove(self, path: str) -> None:
        self.calls.append(("remove", path))
        if self.fail_remove:
            raise self.fail_remove
        self.files.pop(path, None)

    def stat(self, path: str) -> _Attrs:
        self.calls.append(("stat", path))
        if self.fail_stat:
            raise self.fail_stat
        if path in self.files:
            return _Attrs(self.files[path])
        if path in self.dirs:
            return _Attrs(0)
        raise FileNotFoundError(path)

    def mkdir(self, path: str) -> None:
        self.calls.append(("mkdir", path))
        if self.fail_mkdir:
            raise self.fail_mkdir
        self.dirs.add(path)

    def put(self, local: str, remote: str) -> None:
        self.calls.append(("put", local, remote))
        if self.fail_put:
            raise self.fail_put
        self.files[remote] = Path(local).stat().st_size

    def get(self, remote: str, local: str) -> None:
        self.calls.append(("get", remote, local))
        if self.fail_get:
            raise self.fail_get
        Path(local).write_bytes(b"x" * self.files.get(remote, 0))

    def close(self) -> None:
        self.closed = True


# --- listdir / remove -------------------------------------------------------


def test_listdir_returns_names() -> None:
    fake = FakeSftp()
    fake.files["a.rsc"] = 10
    fake.files["b.rsc"] = 20
    sftp = SftpClient(fake)
    assert sorted(sftp.listdir(".")) == ["a.rsc", "b.rsc"]


def test_listdir_wraps_io_error() -> None:
    fake = FakeSftp()
    fake.fail_listdir = IOError("permission denied")
    sftp = SftpClient(fake)
    with pytest.raises(SftpError, match="listdir failed"):
        sftp.listdir(".")


def test_remove_calls_underlying() -> None:
    fake = FakeSftp()
    fake.files["x.rsc"] = 1
    SftpClient(fake).remove("x.rsc")
    assert "x.rsc" not in fake.files
    assert ("remove", "x.rsc") in fake.calls


def test_remove_wraps_io_error() -> None:
    fake = FakeSftp()
    fake.fail_remove = IOError("nope")
    with pytest.raises(SftpError, match="remove failed"):
        SftpClient(fake).remove("x.rsc")


# --- ensure_dir -------------------------------------------------------------


def test_ensure_dir_creates_missing_segments() -> None:
    fake = FakeSftp()
    SftpClient(fake).ensure_dir("staged/2026/apply")
    # Each segment was checked, then created.
    mkdirs = [c[1] for c in fake.calls if c[0] == "mkdir"]
    assert mkdirs == ["staged", "staged/2026", "staged/2026/apply"]


def test_ensure_dir_skips_existing_segments() -> None:
    fake = FakeSftp()
    fake.dirs = {"staged", "staged/2026"}
    SftpClient(fake).ensure_dir("staged/2026/apply")
    mkdirs = [c[1] for c in fake.calls if c[0] == "mkdir"]
    assert mkdirs == ["staged/2026/apply"]


def test_ensure_dir_idempotent_when_all_present() -> None:
    fake = FakeSftp()
    fake.dirs = {"a", "a/b"}
    SftpClient(fake).ensure_dir("a/b")
    assert all(c[0] != "mkdir" for c in fake.calls)


def test_ensure_dir_wraps_mkdir_failure() -> None:
    fake = FakeSftp()
    fake.fail_mkdir = IOError("disk full")
    with pytest.raises(SftpError, match="mkdir failed"):
        SftpClient(fake).ensure_dir("staged")


def test_ensure_dir_wraps_unexpected_stat_failure() -> None:
    fake = FakeSftp()
    fake.fail_stat = IOError("permission denied")
    with pytest.raises(SftpError, match="stat failed"):
        SftpClient(fake).ensure_dir("staged")


# --- put / get / stat_size --------------------------------------------------


def test_put_uploads_file(tmp_path: Path) -> None:
    src = tmp_path / "down.rsc"
    src.write_bytes(b"hello world")
    fake = FakeSftp()
    SftpClient(fake).put(src, "apply.rsc")
    assert fake.files["apply.rsc"] == 11


def test_put_wraps_failure(tmp_path: Path) -> None:
    src = tmp_path / "x.rsc"
    src.write_bytes(b"x")
    fake = FakeSftp()
    fake.fail_put = paramiko.SSHException("channel reset")
    with pytest.raises(SftpError, match="put failed"):
        SftpClient(fake).put(src, "x.rsc")


def test_get_downloads_file(tmp_path: Path) -> None:
    fake = FakeSftp()
    fake.files["apply.rsc"] = 4
    dst = tmp_path / "out.rsc"
    SftpClient(fake).get("apply.rsc", dst)
    assert dst.read_bytes() == b"xxxx"


def test_get_wraps_failure(tmp_path: Path) -> None:
    fake = FakeSftp()
    fake.fail_get = IOError("no such file")
    with pytest.raises(SftpError, match="get failed"):
        SftpClient(fake).get("missing.rsc", tmp_path / "out.rsc")


def test_stat_size_returns_int() -> None:
    fake = FakeSftp()
    fake.files["apply.rsc"] = 1234
    assert SftpClient(fake).stat_size("apply.rsc") == 1234


def test_stat_size_wraps_failure() -> None:
    fake = FakeSftp()
    fake.fail_stat = IOError("missing")
    with pytest.raises(SftpError, match="stat failed"):
        SftpClient(fake).stat_size("missing.rsc")


# --- context manager / close ------------------------------------------------


def test_context_manager_closes_channel() -> None:
    fake = FakeSftp()
    with SftpClient(fake):
        pass
    assert fake.closed is True


def test_close_is_explicit() -> None:
    fake = FakeSftp()
    SftpClient(fake).close()
    assert fake.closed is True
