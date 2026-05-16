"""Tests for ``mtctl.remove.remove_remote``."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mtctl import remove as remove_mod  # noqa: E402
from mtctl.config import Settings  # noqa: E402
from mtctl.remove import RemoveError, remove_remote  # noqa: E402
from mtctl.sftp import SftpError  # noqa: E402


# --- fakes -----------------------------------------------------------------


class FakeSftp:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.fail_with: SftpError | None = None

    def remove(self, path: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.removed.append(path)

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeSftp":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class FakeSshSession:
    last: "FakeSshSession | None" = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sftp = FakeSftp()
        FakeSshSession.last = self

    def __enter__(self) -> "FakeSshSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def open_sftp(self) -> FakeSftp:
        return self.sftp


@pytest.fixture(autouse=True)
def patch_session(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSshSession.last = None
    monkeypatch.setattr(remove_mod, "SshSession", FakeSshSession)


def _settings(**overrides: Any) -> Settings:
    base = dict(host="10.0.0.1", user="admin", password="pw", port=22, timeout=10.0)
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- happy path ------------------------------------------------------------


def test_remove_calls_sftp_unlink() -> None:
    remove_remote("tmp/probe.rsc", _settings())
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.sftp.removed == ["tmp/probe.rsc"]


def test_dry_run_skips_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="mtctl")
    remove_remote("tmp/probe.rsc", _settings(), dry_run=True)
    assert FakeSshSession.last is None
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "DRY RUN: would remove: tmp/probe.rsc" in text


# --- error paths -----------------------------------------------------------


def test_empty_path_rejected() -> None:
    with pytest.raises(RemoveError, match="must not be empty"):
        remove_remote("", _settings())
    assert FakeSshSession.last is None


def test_sftp_error_wrapped_as_remove_error() -> None:
    """SFTP exceptions surface as RemoveError so the CLI exit-1 fires."""
    # We need the FakeSftp to fail when .remove is called -- but the
    # fixture creates a fresh FakeSshSession only on construction by
    # remove_remote. Patch the class to install a pre-failing sftp.
    real_init = FakeSshSession.__init__

    def init_with_failing_sftp(self, settings):  # noqa: ANN001
        real_init(self, settings)
        self.sftp.fail_with = SftpError("permission denied")

    FakeSshSession.__init__ = init_with_failing_sftp  # type: ignore[method-assign]
    try:
        with pytest.raises(RemoveError, match="permission denied"):
            remove_remote("locked.rsc", _settings())
    finally:
        FakeSshSession.__init__ = real_init  # type: ignore[method-assign]
