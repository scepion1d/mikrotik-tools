"""CLI smoke tests for ``rsc bundle``.

Drives :func:`rsc.bundle.cli.main` directly with arg lists and verifies
output paths, exit codes, and the two output modes (default pipeline and
``--no-flatten``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.bundle.cli import main as bundle_main  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


# --- successful runs --------------------------------------------------------


def test_writes_to_explicit_file_path(tmp_path: Path) -> None:
    out = tmp_path / "bundle.rsc"
    rc = bundle_main(["--profile", str(FIX / "profile"), "-o", str(out)])
    assert rc == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    # Default pipeline ran: secrets/vars resolved, no `:global` lines left.
    assert ":global" not in text
    # Some recognisable content from the profile fixture survived.
    assert "/interface/list" in text or "/system/identity" in text


def test_writes_to_existing_directory_with_auto_name(tmp_path: Path) -> None:
    """``-o <existing-dir>`` -> ``<dir>/<profile>-<yymmdd>-<secs>.rsc``."""
    out_dir = tmp_path / "builds"
    out_dir.mkdir()
    rc = bundle_main(["--profile", str(FIX / "profile"), "-o", str(out_dir)])
    assert rc == 0
    files = list(out_dir.glob("profile-*.rsc"))
    assert len(files) == 1, list(out_dir.iterdir())
    assert re.match(r"profile-\d{6}-\d+\.rsc", files[0].name)


def test_bare_name_treated_as_directory(tmp_path: Path) -> None:
    """``-o builds`` with no existing file -> create dir, auto-name inside."""
    out_dir = tmp_path / "newdir"
    rc = bundle_main(["--profile", str(FIX / "profile"), "-o", str(out_dir)])
    assert rc == 0
    assert out_dir.is_dir()
    assert any(out_dir.glob("profile-*.rsc"))


def test_default_out_is_dot_out_under_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``-o`` -> ``./out/<profile>-<stamp>.rsc`` under cwd."""
    monkeypatch.chdir(tmp_path)
    rc = bundle_main(["--profile", str(FIX / "profile")])
    assert rc == 0
    out_dir = tmp_path / "out"
    assert out_dir.is_dir()
    assert any(out_dir.glob("profile-*.rsc"))


def test_no_flatten_keeps_globals_and_banners(tmp_path: Path) -> None:
    out = tmp_path / "raw.rsc"
    rc = bundle_main([
        "--profile", str(FIX / "profile"),
        "-o", str(out),
        "--no-flatten",
    ])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    # Raw concat keeps :global lines (no flatten pass) and per-file banners.
    assert ":global" in text
    assert "begin secrets.rsc" in text


def test_short_o_flag_alias(tmp_path: Path) -> None:
    """``-o`` is the short alias for ``--out``."""
    out = tmp_path / "bundle.rsc"
    rc = bundle_main(["--profile", str(FIX / "profile"), "-o", str(out)])
    assert rc == 0
    assert out.is_file()


def test_prints_output_path_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI prints the resolved output path so chained scripts can capture it."""
    out = tmp_path / "bundle.rsc"
    bundle_main(["--profile", str(FIX / "profile"), "-o", str(out)])
    printed = capsys.readouterr().out.strip()
    # Compare via Path so backslash/forward-slash differences don't matter.
    assert Path(printed) == out


# --- failure paths ----------------------------------------------------------


def test_missing_profile_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = bundle_main(["--profile", str(tmp_path / "no-such-dir")])
    assert rc == 2
    assert "profile folder not found" in capsys.readouterr().err


def test_empty_profile_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """``LoaderError`` from an empty profile folder maps to exit 2."""
    empty = tmp_path / "empty-profile"
    empty.mkdir()
    rc = bundle_main(["--profile", str(empty)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "rsc bundle:" in err
    assert "no .rsc" in err.lower() or "no rsc" in err.lower()


def test_missing_required_profile_flag_exits_2() -> None:
    """argparse exits 2 when ``--profile`` is omitted."""
    with pytest.raises(SystemExit) as exc:
        bundle_main([])
    assert exc.value.code == 2
