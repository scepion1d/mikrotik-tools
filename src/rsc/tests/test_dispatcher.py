"""Tests for the top-level ``rsc`` CLI dispatcher."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from rsc import cli as cli_mod  # noqa: E402
from rsc.cli import main as cli_main  # noqa: E402


# --- usage line / no subcommand --------------------------------------------


def test_no_args_prints_usage_and_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "usage: rsc" in err
    assert "bundle" in err
    assert "diff" in err
    assert "reverse" in err


def test_help_flag_prints_usage_and_returns_0(capsys: pytest.CaptureFixture[str]) -> None:
    for flag in ("-h", "--help"):
        rc = cli_main([flag])
        assert rc == 0, flag
        out = capsys.readouterr().out
        assert "usage: rsc" in out


def test_unknown_subcommand_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["frobnicate"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown subcommand" in err
    assert "frobnicate" in err
    # Usage hint follows the error.
    assert "bundle" in err and "diff" in err and "reverse" in err


# --- dispatch ---------------------------------------------------------------


def test_bundle_dispatches_with_remaining_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 7

    # Monkeypatch the *module* function the dispatcher imports lazily.
    import rsc.bundle.cli as bundle_cli

    monkeypatch.setattr(bundle_cli, "main", fake_main)

    rc = cli_main(["bundle", "--profile", "rsc/basic", "-o", "out.rsc"])
    assert rc == 7
    assert captured["argv"] == ["--profile", "rsc/basic", "-o", "out.rsc"]


def test_diff_dispatches_with_remaining_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    import rsc.diff.cli as diff_cli

    monkeypatch.setattr(diff_cli, "main", fake_main)

    rc = cli_main(["diff", "--old", "a.rsc", "--new", "b.rsc", "--check"])
    assert rc == 0
    assert captured["argv"] == ["--old", "a.rsc", "--new", "b.rsc", "--check"]


def test_reverse_dispatches_with_remaining_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    import rsc.yaml.reverse_cli as reverse_cli

    monkeypatch.setattr(reverse_cli, "main", fake_main)

    rc = cli_main(["reverse", "--src", "live.rsc", "-o", "src/new/"])
    assert rc == 0
    assert captured["argv"] == ["--src", "live.rsc", "-o", "src/new/"]


def test_dispatcher_uses_sys_argv_when_argv_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling main() with no argv falls back to sys.argv[1:]."""
    captured: dict[str, Any] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    import rsc.bundle.cli as bundle_cli

    monkeypatch.setattr(bundle_cli, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["rsc", "bundle", "--profile", "x"])

    rc = cli_main()
    assert rc == 0
    assert captured["argv"] == ["--profile", "x"]
