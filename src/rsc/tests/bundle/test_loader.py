"""Tests for the profile-folder loader."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from rsc.bundle.loader import LoaderError, concat, load_profile  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "profile"


def test_load_profile_orders_globals_first() -> None:
    files = load_profile(FIX)
    names = [p.name for p in files]
    # secrets.rsc + vars.rsc must come before any module so that
    # :global assignments are visible during $var substitution.
    assert names[0] == "secrets.rsc"
    assert names[1] == "vars.rsc"
    # Remaining files appear in alphabetical order.
    rest = names[2:]
    assert rest == sorted(rest, key=str.lower)
    assert "10-interfaces.rsc" in rest
    assert "50-services.rsc" in rest
    assert "60-system.rsc" in rest


def test_load_profile_missing_dir_raises() -> None:
    with pytest.raises(LoaderError):
        load_profile(FIX / "does-not-exist")


def test_load_profile_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(LoaderError):
        load_profile(tmp_path)


def test_load_profile_ignores_non_rsc(tmp_path: Path) -> None:
    (tmp_path / "10-foo.rsc").write_text("/system/clock\n    set time-zone-name=UTC\n")
    (tmp_path / "README.md").write_text("docs\n")
    (tmp_path / "notes.txt").write_text("ignored\n")
    files = load_profile(tmp_path)
    assert [p.name for p in files] == ["10-foo.rsc"]


def test_load_profile_skips_subdirectories(tmp_path: Path) -> None:
    (tmp_path / "10-foo.rsc").write_text("/system/clock\n    set time-zone-name=UTC\n")
    sub = tmp_path / "modules"
    sub.mkdir()
    (sub / "20-bar.rsc").write_text("/system/identity\n    set name=x\n")
    files = load_profile(tmp_path)
    assert [p.name for p in files] == ["10-foo.rsc"]


def test_load_profile_works_without_globals(tmp_path: Path) -> None:
    """secrets.rsc and vars.rsc are optional; loader still succeeds."""
    (tmp_path / "10-foo.rsc").write_text("/system/clock\n    set time-zone-name=UTC\n")
    files = load_profile(tmp_path)
    assert [p.name for p in files] == ["10-foo.rsc"]


def test_concat_preserves_file_order_and_content() -> None:
    files = load_profile(FIX)
    text = concat(files)
    # Banner markers identify each file.
    assert "# >>> begin secrets.rsc" in text
    assert "# >>> begin vars.rsc" in text
    assert "# >>> begin 10-interfaces.rsc" in text
    # Contents survive concatenation.
    assert ":global adminPass" in text
    assert ":global adminCidrs" in text
    assert "/interface/list" in text
    # Ordering: secrets.rsc banner before vars.rsc banner.
    assert text.index("begin secrets.rsc") < text.index("begin vars.rsc")
    assert text.index("begin vars.rsc") < text.index("begin 10-interfaces.rsc")
