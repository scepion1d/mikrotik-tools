"""Tests for ``mtctl.backup.create_backup``.

We replace :class:`mtctl.ssh.SshSession` with an in-memory fake
that exposes ``exec`` (recorded) and ``open_sftp`` -> a tiny SFTP fake
that records ``ensure_dir`` calls. Network is never touched.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mtctl import backup as backup_mod  # noqa: E402
from mtctl.backup import BackupError, create_backup  # noqa: E402
from mtctl.config import Settings  # noqa: E402


# --- fakes ------------------------------------------------------------------


class FakeSftp:
    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.closed = False

    def ensure_dir(self, path: str) -> None:
        self.ensured.append(path)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeSftp":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class FakeSshSession:
    """Stand-in for SshSession with a programmable exec().

    Each exec() call returns the next entry from `exec_results`
    (`(status, stdout, stderr)`). If the queue is empty, returns
    `(0, "", "")` -- the success default.
    """

    last: "FakeSshSession | None" = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sftp = FakeSftp()
        self.exec_calls: list[str] = []
        self.exec_results: list[tuple[int, str, str]] = []
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

    def exec(self, command: str, *, timeout: float | None = None) -> tuple[int, str, str]:
        self.exec_calls.append(command)
        if self.exec_results:
            return self.exec_results.pop(0)
        return (0, "", "")


@pytest.fixture(autouse=True)
def patch_session(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSshSession.last = None
    monkeypatch.setattr(backup_mod, "SshSession", FakeSshSession)


def _settings(**overrides: Any) -> Settings:
    base = dict(host="10.0.0.1", user="admin", password="pw", port=22, timeout=10.0)
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- happy path -------------------------------------------------------------


def test_create_backup_runs_two_commands_in_order() -> None:
    folder = create_backup(_settings(), timestamp="20260511-120000")
    sess = FakeSshSession.last
    assert sess is not None
    assert folder == "backups/20260511-120000"
    assert sess.exec_calls == [
        '/system/backup save name="backups/20260511-120000/live" dont-encrypt=yes',
        '/export show-sensitive file="backups/20260511-120000/live"',
    ]


def test_create_backup_ensures_remote_folder() -> None:
    create_backup(_settings(), timestamp="20260511-120000")
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.sftp.ensured == ["backups/20260511-120000"]


def test_create_backup_uses_password_when_given() -> None:
    create_backup(_settings(), password="s3cret", timestamp="20260511-120000")
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.exec_calls[0] == (
        '/system/backup save name="backups/20260511-120000/live" password="s3cret"'
    )
    # No dont-encrypt token in the encrypted path.
    assert "dont-encrypt" not in sess.exec_calls[0]


def test_create_backup_default_timestamp_is_yyyymmdd_hhmmss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_mod, "_now_utc_stamp", lambda: "21000101-000000")
    folder = create_backup(_settings())
    assert folder == "backups/21000101-000000"


def test_create_backup_session_lifecycle() -> None:
    create_backup(_settings(), timestamp="t")
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.entered is True
    assert sess.exited is True
    assert sess.sftp.closed is True


def test_create_backup_returns_folder_path() -> None:
    folder = create_backup(_settings(), timestamp="abc123")
    assert folder == "backups/abc123"


# --- dry run ----------------------------------------------------------------


def test_dry_run_skips_session_but_returns_folder(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="mtctl")
    folder = create_backup(_settings(), timestamp="t", dry_run=True)
    assert folder == "backups/t"
    assert FakeSshSession.last is None
    # Both commands and the remote folder are reported.
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "would ensure remote directory: backups/t" in text
    assert "/system/backup save" in text
    assert "/export show-sensitive" in text


# --- failure surfaces -------------------------------------------------------


def test_failure_in_stdout_raises() -> None:
    """RouterOS reports most errors as `failure: ...` on stdout."""
    sess = FakeSshSession(_settings())
    sess.exec_results = [
        (0, "failure: file already exists\n", ""),
        (0, "", ""),  # never reached
    ]
    FakeSshSession.last = sess
    backup_mod.SshSession = (lambda _s: sess)  # type: ignore[assignment]
    try:
        with pytest.raises(BackupError, match="backup save failed"):
            create_backup(_settings(), timestamp="t")
    finally:
        # Restored by the autouse fixture for the next test.
        backup_mod.SshSession = FakeSshSession  # type: ignore[assignment]


def test_nonzero_exit_status_raises() -> None:
    sess = FakeSshSession(_settings())
    sess.exec_results = [(1, "", "syntax error\n")]
    FakeSshSession.last = sess
    backup_mod.SshSession = (lambda _s: sess)  # type: ignore[assignment]
    try:
        with pytest.raises(BackupError, match="backup save failed .exit 1."):
            create_backup(_settings(), timestamp="t")
    finally:
        backup_mod.SshSession = FakeSshSession  # type: ignore[assignment]


def test_failure_in_export_step_raises() -> None:
    sess = FakeSshSession(_settings())
    sess.exec_results = [
        (0, "", ""),                             # backup save OK
        (0, "failure: cannot open file\n", ""),  # export fails
    ]
    FakeSshSession.last = sess
    backup_mod.SshSession = (lambda _s: sess)  # type: ignore[assignment]
    try:
        with pytest.raises(BackupError, match="export failed"):
            create_backup(_settings(), timestamp="t")
    finally:
        backup_mod.SshSession = FakeSshSession  # type: ignore[assignment]


def test_password_with_double_quote_rejected() -> None:
    with pytest.raises(BackupError, match="double quotes"):
        create_backup(_settings(), password='evil"pw', timestamp="t")
    # Defensive: this fails before any session is opened.
    assert FakeSshSession.last is None
