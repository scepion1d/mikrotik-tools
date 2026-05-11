"""Tests for ``mtctl.deployer.upload`` and ``download``.

We replace :class:`mtctl.ssh.SshSession` with a fake that yields an
in-memory :class:`FakeSftp` (also used by ``test_sftp.py``-like fakes
here, kept local to avoid cross-test imports). Network is never touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mtctl import deployer as deployer_mod  # noqa: E402
from mtctl.config import Settings  # noqa: E402
from mtctl.deployer import DeployError, download, upload  # noqa: E402


# --- in-memory fakes --------------------------------------------------------


class FakeSftp:
    """In-memory SFTP. Stores per-path bytes; tracks ensure_dir calls."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.ensured_dirs: list[str] = []
        self.put_calls: list[tuple[Path, str]] = []
        self.get_calls: list[tuple[str, Path]] = []
        self.closed = False

    def ensure_dir(self, path: str) -> None:
        self.ensured_dirs.append(path)

    def put(self, local: Path, remote: str) -> None:
        self.put_calls.append((local, remote))
        self.files[remote] = Path(local).read_bytes()

    def get(self, remote: str, local: Path) -> None:
        self.get_calls.append((remote, local))
        Path(local).write_bytes(self.files.get(remote, b""))

    def stat_size(self, remote: str) -> int:
        if remote not in self.files:
            raise AssertionError(f"stat on missing remote: {remote}")
        return len(self.files[remote])

    def close(self) -> None:
        self.closed = True

    # Context-manager protocol -- mirrors the real SftpClient.
    def __enter__(self) -> "FakeSftp":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class FakeSshSession:
    """Stand-in for SshSession that yields a FakeSftp on `open_sftp`."""

    last: "FakeSshSession | None" = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sftp = FakeSftp()
        self.entered = False
        self.exited = False
        FakeSshSession.last = self

    def __enter__(self) -> "FakeSshSession":
        self.entered = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exited = True

    def open_sftp(self) -> FakeSftp:
        return self.sftp


@pytest.fixture(autouse=True)
def patch_session(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSshSession.last = None
    monkeypatch.setattr(deployer_mod, "SshSession", FakeSshSession)


def _settings(**overrides: Any) -> Settings:
    base = dict(host="10.0.0.1", user="admin", password="pw", port=22, timeout=10.0)
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- upload: validation -----------------------------------------------------


def test_upload_missing_src_raises(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match="not found"):
        upload(tmp_path / "nope.rsc", "apply.rsc", _settings())


def test_upload_directory_src_rejected(tmp_path: Path) -> None:
    """Single-file API: a directory is not a valid src."""
    with pytest.raises(DeployError, match="not a regular file"):
        upload(tmp_path, "apply.rsc", _settings())


def test_upload_empty_dst_rejected(tmp_path: Path) -> None:
    src = tmp_path / "x.rsc"
    src.write_bytes(b"x")
    with pytest.raises(DeployError, match="empty"):
        upload(src, "", _settings())


def test_upload_dst_with_trailing_slash_rejected(tmp_path: Path) -> None:
    src = tmp_path / "x.rsc"
    src.write_bytes(b"x")
    with pytest.raises(DeployError, match="must include a filename"):
        upload(src, "staged/", _settings())


# --- upload: behaviour ------------------------------------------------------


def test_upload_writes_file_to_root(tmp_path: Path) -> None:
    src = tmp_path / "down.rsc"
    src.write_bytes(b"hello")
    upload(src, "apply.rsc", _settings())
    sftp = FakeSshSession.last.sftp  # type: ignore[union-attr]
    assert sftp.files == {"apply.rsc": b"hello"}
    assert sftp.ensured_dirs == []  # no parent at root


def test_upload_creates_remote_parent(tmp_path: Path) -> None:
    src = tmp_path / "down.rsc"
    src.write_bytes(b"hi")
    upload(src, "staged/2026-05-11/apply.rsc", _settings())
    sftp = FakeSshSession.last.sftp  # type: ignore[union-attr]
    assert sftp.ensured_dirs == ["staged/2026-05-11"]
    assert sftp.files == {"staged/2026-05-11/apply.rsc": b"hi"}


def test_upload_normalizes_backslashes_and_leading_slash(tmp_path: Path) -> None:
    src = tmp_path / "down.rsc"
    src.write_bytes(b"hi")
    upload(src, "/staged\\inner\\apply.rsc", _settings())
    sftp = FakeSshSession.last.sftp  # type: ignore[union-attr]
    assert "staged/inner/apply.rsc" in sftp.files
    assert sftp.ensured_dirs == ["staged/inner"]


def test_upload_overwrites_existing_remote(tmp_path: Path) -> None:
    src = tmp_path / "down.rsc"
    src.write_bytes(b"v2")
    # Pre-populate via first upload, then overwrite.
    upload(src, "apply.rsc", _settings())
    src.write_bytes(b"v3")
    upload(src, "apply.rsc", _settings())
    sftp = FakeSshSession.last.sftp  # type: ignore[union-attr]
    assert sftp.files["apply.rsc"] == b"v3"


def test_upload_dry_run_skips_session(tmp_path: Path) -> None:
    src = tmp_path / "down.rsc"
    src.write_bytes(b"hi")
    upload(src, "staged/apply.rsc", _settings(), dry_run=True)
    assert FakeSshSession.last is None


def test_upload_session_lifecycle(tmp_path: Path) -> None:
    """Session is entered + exited; SFTP is closed in the same with-block."""
    src = tmp_path / "down.rsc"
    src.write_bytes(b"hi")
    upload(src, "apply.rsc", _settings())
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.entered is True
    assert sess.exited is True
    assert sess.sftp.closed is True


# --- download: validation ---------------------------------------------------


def test_download_empty_src_rejected(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match="empty"):
        download("", tmp_path / "out.rsc", _settings())


def test_download_dst_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match="directory"):
        download("apply.rsc", tmp_path, _settings())


# --- download: behaviour ----------------------------------------------------


def test_download_writes_local_file(tmp_path: Path) -> None:
    # Pre-load a remote file via an explicit session creation.
    fake = FakeSshSession(_settings())
    fake.sftp.files["apply.rsc"] = b"payload"
    FakeSshSession.last = fake
    # Patch SshSession constructor to return our preloaded session ONCE.
    original = deployer_mod.SshSession

    def fixed(_settings_arg: Settings) -> FakeSshSession:
        return fake

    deployer_mod.SshSession = fixed  # type: ignore[assignment]
    try:
        dst = tmp_path / "out.rsc"
        download("apply.rsc", dst, _settings())
        assert dst.read_bytes() == b"payload"
    finally:
        deployer_mod.SshSession = original  # type: ignore[assignment]


def test_download_creates_local_parent(tmp_path: Path) -> None:
    fake = FakeSshSession(_settings())
    fake.sftp.files["apply.rsc"] = b"payload"
    FakeSshSession.last = fake
    original = deployer_mod.SshSession
    deployer_mod.SshSession = (lambda _s: fake)  # type: ignore[assignment]
    try:
        dst = tmp_path / "deep" / "nested" / "out.rsc"
        download("apply.rsc", dst, _settings())
        assert dst.read_bytes() == b"payload"
        assert dst.parent.is_dir()
    finally:
        deployer_mod.SshSession = original  # type: ignore[assignment]


def test_download_overwrites_existing_local(tmp_path: Path) -> None:
    dst = tmp_path / "out.rsc"
    dst.write_bytes(b"old")
    fake = FakeSshSession(_settings())
    fake.sftp.files["apply.rsc"] = b"new"
    FakeSshSession.last = fake
    original = deployer_mod.SshSession
    deployer_mod.SshSession = (lambda _s: fake)  # type: ignore[assignment]
    try:
        download("apply.rsc", dst, _settings())
        assert dst.read_bytes() == b"new"
    finally:
        deployer_mod.SshSession = original  # type: ignore[assignment]


def test_download_normalizes_remote_path(tmp_path: Path) -> None:
    fake = FakeSshSession(_settings())
    fake.sftp.files["staged/apply.rsc"] = b"x"
    FakeSshSession.last = fake
    original = deployer_mod.SshSession
    deployer_mod.SshSession = (lambda _s: fake)  # type: ignore[assignment]
    try:
        download("/staged\\apply.rsc", tmp_path / "out.rsc", _settings())
        assert (tmp_path / "out.rsc").read_bytes() == b"x"
    finally:
        deployer_mod.SshSession = original  # type: ignore[assignment]


def test_download_dry_run_skips_session(tmp_path: Path) -> None:
    download("apply.rsc", tmp_path / "out.rsc", _settings(), dry_run=True)
    assert FakeSshSession.last is None
