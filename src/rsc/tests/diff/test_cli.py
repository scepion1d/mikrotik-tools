"""CLI smoke tests for rsc.diff (single-patch + roundtrip modes)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.diff.cli import main as diff_main  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


# --- single-patch mode ------------------------------------------------------


def test_single_patch_writes_to_explicit_file(tmp_path: Path) -> None:
    out = tmp_path / "patch.rsc"
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
        "--out", str(out),
    ])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "/interface/list" in text
    # Ops appear in the patch.
    assert "add" in text
    # Header records both inputs.
    assert "minimal_a.rsc" in text
    assert "minimal_b.rsc" in text


def test_single_patch_short_out_flag_works(tmp_path: Path) -> None:
    """`-o` is the short alias for `--out`."""
    out = tmp_path / "patch.rsc"
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
        "-o", str(out),
    ])
    assert rc == 0
    assert out.is_file()
    assert out.read_text(encoding="utf-8")  # non-empty


def test_single_patch_auto_names_in_directory(tmp_path: Path) -> None:
    """`--out <existing-dir>` -> auto-named ``<old>-<new>-<stamp>.rsc``."""
    out_dir = tmp_path / "patches"
    out_dir.mkdir()
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
        "--out", str(out_dir),
    ])
    assert rc == 0
    files = list(out_dir.glob("minimal_a-minimal_b-*.rsc"))
    assert len(files) == 1, files
    # Stamp suffix is 6-digit date + dash + integer seconds.
    name = files[0].name
    assert re.match(r"minimal_a-minimal_b-\d{6}-\d+\.rsc", name), name


def test_single_patch_default_out_is_dot_out(tmp_path: Path, monkeypatch) -> None:
    """No --out -> ``./out/<old>-<new>-<stamp>.rsc`` (under cwd)."""
    monkeypatch.chdir(tmp_path)
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
    ])
    assert rc == 0
    out_dir = tmp_path / "out"
    assert out_dir.is_dir()
    files = list(out_dir.glob("minimal_a-minimal_b-*.rsc"))
    assert len(files) == 1, list(out_dir.iterdir())


def test_check_mode_returns_1_on_drift(tmp_path: Path) -> None:
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
        "--check",
    ])
    assert rc == 1


def test_check_mode_returns_0_on_no_drift(tmp_path: Path) -> None:
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_a.rsc"),
        "--check",
    ])
    assert rc == 0


def test_missing_old_file_returns_2() -> None:
    rc = diff_main(["--old", "no-such-file.rsc", "--new", str(FIX / "minimal_a.rsc")])
    assert rc == 2


def test_missing_new_file_returns_2() -> None:
    rc = diff_main(["--old", str(FIX / "minimal_a.rsc"), "--new", "nope.rsc"])
    assert rc == 2


def test_missing_required_flags_exits(capsys) -> None:
    """argparse exits 2 when --old/--new aren't given."""
    with pytest.raises(SystemExit) as exc_info:
        diff_main([])
    assert exc_info.value.code == 2


# --- roundtrip mode ---------------------------------------------------------


def test_roundtrip_writes_both_patches(tmp_path: Path, capsys) -> None:
    fwd = tmp_path / "fwd.rsc"
    bwd = tmp_path / "bwd.rsc"
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
        "--rollforward", str(fwd),
        "--rollback", str(bwd),
    ])
    assert rc == 0, capsys.readouterr().out
    assert fwd.is_file()
    assert bwd.is_file()
    fwd_text = fwd.read_text(encoding="utf-8")
    bwd_text = bwd.read_text(encoding="utf-8")
    # Headers are direction-correct (basename match; paths in header are absolute).
    assert "rollforward:" in fwd_text
    assert "minimal_a.rsc" in fwd_text and "minimal_b.rsc" in fwd_text
    assert fwd_text.index("minimal_a.rsc") < fwd_text.index("minimal_b.rsc")
    assert "rollback:" in bwd_text
    assert "minimal_a.rsc" in bwd_text and "minimal_b.rsc" in bwd_text
    assert bwd_text.index("minimal_b.rsc") < bwd_text.index("minimal_a.rsc")
    # Each patch has at least one op (drift exists between A and B).
    for text in (fwd_text, bwd_text):
        assert any(
            line.strip().startswith(("add ", "set ", "remove", "reset "))
            for line in text.splitlines()
        ), text


def test_roundtrip_self_diff_succeeds(tmp_path: Path) -> None:
    """A == B: both patches empty, both legs verify clean."""
    fwd = tmp_path / "fwd.rsc"
    bwd = tmp_path / "bwd.rsc"
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_a.rsc"),
        "--rollforward", str(fwd),
        "--rollback", str(bwd),
    ])
    assert rc == 0


def test_roundtrip_requires_both_legs(tmp_path: Path) -> None:
    """Specifying only one of --rollforward/--rollback is a usage error."""
    rc = diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
        "--rollforward", str(tmp_path / "fwd.rsc"),
    ])
    assert rc == 2


def test_roundtrip_reports_per_leg_on_stdout(tmp_path: Path, capsys) -> None:
    fwd = tmp_path / "fwd.rsc"
    bwd = tmp_path / "bwd.rsc"
    diff_main([
        "--old", str(FIX / "minimal_a.rsc"),
        "--new", str(FIX / "minimal_b.rsc"),
        "--rollforward", str(fwd),
        "--rollback", str(bwd),
    ])
    out = capsys.readouterr().out
    # Both legs are announced; both should report OK.
    assert "apply(minimal_a.rsc, fwd.rsc) == minimal_b.rsc" in out
    assert "apply(minimal_b.rsc, bwd.rsc) == minimal_a.rsc" in out
    assert out.count("OK -- differ reports no residual drift") == 2
