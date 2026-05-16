"""Tests for ``mtctl.export.export_config``.

Same fake-SshSession pattern as test_backup.py. Network is never touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mtctl import export as export_mod  # noqa: E402
from mtctl.config import Settings  # noqa: E402
from mtctl.export import ExportError, export_config  # noqa: E402


# --- fakes ------------------------------------------------------------------


class FakeSshSession:
    """Same shape as test_backup.py -- programmable exec() with a queue."""

    last: "FakeSshSession | None" = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
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

    def exec(self, command: str, *, timeout: float | None = None) -> tuple[int, str, str]:
        self.exec_calls.append(command)
        if self.exec_results:
            return self.exec_results.pop(0)
        return (0, "", "")


@pytest.fixture(autouse=True)
def patch_session(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSshSession.last = None
    monkeypatch.setattr(export_mod, "SshSession", FakeSshSession)


def _settings(**overrides: Any) -> Settings:
    base = dict(host="10.0.0.1", user="admin", password="pw", port=22, timeout=10.0)
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- happy path -------------------------------------------------------------


def test_export_runs_show_sensitive_by_default(tmp_path: Path) -> None:
    sample = "/system/identity\n    set name=router\n"
    dst = tmp_path / "live.rsc"

    # Prime the fake to return the captured stdout.
    def _instrument():
        sess = FakeSshSession(_settings())
        sess.exec_results = [(0, sample, "")]
        return sess

    # We can't easily pre-instrument since SshSession is created inside
    # export_config; instead, intercept the constructor to install a
    # pre-loaded result on the new instance.
    real_init = FakeSshSession.__init__

    def init_with_payload(self, settings):  # noqa: ANN001
        real_init(self, settings)
        self.exec_results = [(0, sample, "")]

    FakeSshSession.__init__ = init_with_payload  # type: ignore[method-assign]
    try:
        out = export_config(_settings(), dst)
    finally:
        FakeSshSession.__init__ = real_init  # type: ignore[method-assign]

    sess = FakeSshSession.last
    assert sess is not None
    # The command is the show-sensitive form, with NO file= argument
    # (that would write to flash; the point of `export` is to avoid that).
    assert sess.exec_calls == ["/export show-sensitive"]
    assert out == dst
    assert dst.read_text(encoding="utf-8") == sample


def test_export_no_sensitive_flag_drops_show_sensitive(tmp_path: Path) -> None:
    """--no-sensitive omits the `show-sensitive` keyword (PSKs masked)."""
    dst = tmp_path / "redacted.rsc"
    export_config(_settings(), dst, sensitive=False)
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.exec_calls == ["/export"]


def test_export_creates_parent_directory(tmp_path: Path) -> None:
    """Missing parent dirs under --dst are auto-created."""
    dst = tmp_path / "nested" / "deep" / "live.rsc"
    export_config(_settings(), dst)
    assert dst.parent.is_dir()
    assert dst.is_file()


def test_export_normalises_crlf_to_lf(tmp_path: Path) -> None:
    """RouterOS over SSH can emit CRLF; we normalise to LF so the file
    matches what `mtctl download backups/.../live.rsc` would produce."""
    sample_crlf = "/x\r\n    set a=b\r\n"

    def init_with_payload(self, settings):  # noqa: ANN001
        FakeSshSession.__init__.__wrapped__(self, settings)  # type: ignore[attr-defined]
        self.exec_results = [(0, sample_crlf, "")]

    # Simpler: just call export, then manipulate the result directly via
    # a fresh wrapping.
    dst = tmp_path / "live.rsc"
    real_init = FakeSshSession.__init__

    def init_v2(self, settings):  # noqa: ANN001
        real_init(self, settings)
        self.exec_results = [(0, sample_crlf, "")]

    FakeSshSession.__init__ = init_v2  # type: ignore[method-assign]
    try:
        export_config(_settings(), dst)
    finally:
        FakeSshSession.__init__ = real_init  # type: ignore[method-assign]

    text = dst.read_text(encoding="utf-8")
    assert "\r" not in text
    assert text == "/x\n    set a=b\n"


def test_export_dry_run_skips_ssh(tmp_path: Path) -> None:
    """--dry-run: no SSH session is even instantiated."""
    dst = tmp_path / "live.rsc"
    out = export_config(_settings(), dst, dry_run=True)
    assert out == dst
    assert not dst.exists()
    # No session entered (FakeSshSession is patched in but never constructed).
    assert FakeSshSession.last is None


# --- error paths ------------------------------------------------------------


def test_export_raises_on_non_zero_status(tmp_path: Path) -> None:
    dst = tmp_path / "live.rsc"
    real_init = FakeSshSession.__init__

    def init_fail(self, settings):  # noqa: ANN001
        real_init(self, settings)
        self.exec_results = [(1, "", "something went wrong")]

    FakeSshSession.__init__ = init_fail  # type: ignore[method-assign]
    try:
        with pytest.raises(ExportError, match="exit 1"):
            export_config(_settings(), dst)
    finally:
        FakeSshSession.__init__ = real_init  # type: ignore[method-assign]
    assert not dst.exists()


def test_export_raises_on_failure_marker_in_stdout(tmp_path: Path) -> None:
    """RouterOS reports many errors on stdout as `failure: ...`."""
    dst = tmp_path / "live.rsc"
    real_init = FakeSshSession.__init__

    def init_failure(self, settings):  # noqa: ANN001
        real_init(self, settings)
        self.exec_results = [(0, "failure: not allowed\n", "")]

    FakeSshSession.__init__ = init_failure  # type: ignore[method-assign]
    try:
        with pytest.raises(ExportError, match="failure: not allowed"):
            export_config(_settings(), dst)
    finally:
        FakeSshSession.__init__ = real_init  # type: ignore[method-assign]
    assert not dst.exists()
