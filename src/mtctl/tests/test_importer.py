"""Tests for ``mtctl.importer.run_import``.

Same fake-session pattern as :mod:`tests.test_backup`. No network is touched.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mtctl import importer as importer_mod  # noqa: E402
from mtctl.config import Settings  # noqa: E402
from mtctl.importer import ImportError, run_import  # noqa: E402


# --- fakes ------------------------------------------------------------------


class FakeSshSession:
    """Stand-in for SshSession with a programmable exec().

    Each exec() returns the next entry from `exec_results` as
    `(status, stdout, stderr)`. Empty queue -> `(0, "", "")` (success).
    """

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
    monkeypatch.setattr(importer_mod, "SshSession", FakeSshSession)


def _settings(**overrides: Any) -> Settings:
    base = dict(host="10.0.0.1", user="admin", password="pw", port=22, timeout=10.0)
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- happy path -------------------------------------------------------------


def test_run_import_emits_verbose_command_by_default() -> None:
    run_import("deployment/20260511-100000/up.rsc", _settings())
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.exec_calls == [
        '/import file-name="deployment/20260511-100000/up.rsc" verbose=yes',
    ]


def test_run_import_quiet_mode_drops_verbose_token() -> None:
    run_import("apply.rsc", _settings(), verbose=False)
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.exec_calls == ['/import file-name="apply.rsc"']


def test_run_import_returns_router_output() -> None:
    sess = FakeSshSession(_settings())
    sess.exec_results = [(0, "Script file loaded and executed successfully\n", "")]
    FakeSshSession.last = sess
    importer_mod.SshSession = (lambda _s: sess)  # type: ignore[assignment]
    try:
        out = run_import("apply.rsc", _settings())
    finally:
        importer_mod.SshSession = FakeSshSession  # type: ignore[assignment]
    assert "loaded and executed" in out


def test_run_import_session_lifecycle() -> None:
    run_import("apply.rsc", _settings())
    sess = FakeSshSession.last
    assert sess is not None
    assert sess.entered is True
    assert sess.exited is True


# --- dry run ----------------------------------------------------------------


def test_dry_run_skips_session_and_logs_command(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="mtctl")
    out = run_import("deployment/x/up.rsc", _settings(), dry_run=True)
    assert out == ""
    assert FakeSshSession.last is None
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert '/import file-name="deployment/x/up.rsc" verbose=yes' in text


def test_dry_run_with_quiet_flag(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="mtctl")
    run_import("apply.rsc", _settings(), verbose=False, dry_run=True)
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    # No verbose=yes when --quiet is set.
    assert '/import file-name="apply.rsc"\n' in (text + "\n")
    assert "verbose=yes" not in text


# --- failure surfaces -------------------------------------------------------


def test_failure_in_stdout_raises() -> None:
    """RouterOS reports script errors as `failure: ...` on stdout."""
    sess = FakeSshSession(_settings())
    sess.exec_results = [
        (0, "Opening script file apply.rsc\nfailure: line 5: invalid value\n", ""),
    ]
    FakeSshSession.last = sess
    importer_mod.SshSession = (lambda _s: sess)  # type: ignore[assignment]
    try:
        with pytest.raises(ImportError, match="line 5: invalid value"):
            run_import("apply.rsc", _settings())
    finally:
        importer_mod.SshSession = FakeSshSession  # type: ignore[assignment]


def test_nonzero_exit_status_raises() -> None:
    sess = FakeSshSession(_settings())
    sess.exec_results = [(1, "", "no such item\n")]
    FakeSshSession.last = sess
    importer_mod.SshSession = (lambda _s: sess)  # type: ignore[assignment]
    try:
        with pytest.raises(ImportError, match=r"import failed \(exit 1\)"):
            run_import("missing.rsc", _settings())
    finally:
        importer_mod.SshSession = FakeSshSession  # type: ignore[assignment]


def test_empty_remote_path_rejected() -> None:
    with pytest.raises(ImportError, match="must not be empty"):
        run_import("", _settings())
    assert FakeSshSession.last is None


def test_double_quote_in_remote_path_rejected() -> None:
    with pytest.raises(ImportError, match="double quotes"):
        run_import('weird"name.rsc', _settings())
    assert FakeSshSession.last is None


# --- validate ---------------------------------------------------------------


def _install_session_with_results(results: list[tuple[int, str, str]]) -> "FakeSshSession":
    """Helper: install a FakeSshSession that returns *results* from exec()."""
    sess = FakeSshSession(_settings())
    sess.exec_results = results
    FakeSshSession.last = sess
    importer_mod.SshSession = (lambda _s: sess)  # type: ignore[assignment]
    return sess


def test_validate_file_exists_returns_summary() -> None:
    """File present + 16 KB -> summary mentions size, :parse skipped."""
    sess = _install_session_with_results([
        (0, "*1\n", ""),         # /file find -> internal id
        (0, "16901\n", ""),      # /file get size
    ])
    try:
        out = run_import(
            "deployment/x/up.rsc", _settings(), validate=True,
        )
    finally:
        importer_mod.SshSession = FakeSshSession  # type: ignore[assignment]

    assert "validated:" in out
    assert "16901 bytes" in out
    assert ":parse skipped" in out  # file is bigger than _PARSE_PROBE_LIMIT
    # /import was NOT invoked -- only the two probe commands.
    assert all("/import" not in c for c in sess.exec_calls)


def test_validate_file_missing_raises() -> None:
    """`/file find` returning empty -> file not on flash -> ImportError."""
    _install_session_with_results([
        (0, "\n", ""),  # empty stdout = no rows = file not found
    ])
    try:
        with pytest.raises(ImportError, match="file not found on router"):
            run_import("missing.rsc", _settings(), validate=True)
    finally:
        importer_mod.SshSession = FakeSshSession  # type: ignore[assignment]


def test_validate_small_file_runs_parse_probe_ok() -> None:
    """File under the parse threshold -> :parse runs and reports ok."""
    sess = _install_session_with_results([
        (0, "*5\n", ""),         # find
        (0, "1024\n", ""),       # size (under 3500)
        (0, "parse-ok\n", ""),   # :do {:parse ...} -> parse-ok
    ])
    try:
        out = run_import("small.rsc", _settings(), validate=True)
    finally:
        importer_mod.SshSession = FakeSshSession  # type: ignore[assignment]

    assert ":parse OK" in out
    # Three probes: find, size, parse. No /import.
    assert len(sess.exec_calls) == 3


def test_validate_small_file_parse_failure_raises() -> None:
    """Router's :parse returning `parse-failed` raises ImportError."""
    _install_session_with_results([
        (0, "*9\n", ""),
        (0, "512\n", ""),
        (0, "parse-failed\n", ""),
    ])
    try:
        with pytest.raises(ImportError, match=":parse failed"):
            run_import("broken.rsc", _settings(), validate=True)
    finally:
        importer_mod.SshSession = FakeSshSession  # type: ignore[assignment]


def test_validate_and_dry_run_are_mutually_exclusive() -> None:
    with pytest.raises(ImportError, match="mutually exclusive"):
        run_import("x.rsc", _settings(), dry_run=True, validate=True)
    assert FakeSshSession.last is None
