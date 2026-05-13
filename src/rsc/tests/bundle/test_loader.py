"""Tests for the profile-folder loader."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from rsc.bundle.loader import LoaderError, concat, load_profile  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PROFILE = FIX / "profile"
VARS = FIX  # secrets.rsc + vars.rsc live at fixtures/ root


def test_load_profile_orders_vars_first() -> None:
    files = load_profile(PROFILE, vars_dir=VARS)
    names = [p.name for p in files]
    # All vars files come first (alphabetical), then profile modules.
    # fixtures/ holds secrets.rsc + vars.rsc.
    assert names[0] == "secrets.rsc"
    assert names[1] == "vars.rsc"
    rest = names[2:]
    assert rest == sorted(rest, key=str.lower)
    assert "10-interfaces.rsc" in rest
    assert "50-services.rsc" in rest
    assert "60-system.rsc" in rest


def test_load_profile_without_vars() -> None:
    """vars_dir is optional; loader still returns the modules."""
    files = load_profile(PROFILE)
    names = [p.name for p in files]
    assert "secrets.rsc" not in names
    assert "vars.rsc" not in names
    assert "10-interfaces.rsc" in names


def test_load_profile_loads_every_rsc_in_vars_dir(tmp_path: Path) -> None:
    """Vars folder isn't limited to two specific filenames; everything
    matching ``*.rsc`` at the top level is concatenated in order."""
    vroot = tmp_path / "vars"
    vroot.mkdir()
    (vroot / "a-secrets.rsc").write_text(':global a "1"\n')
    (vroot / "b-vars.rsc").write_text(':global b "2"\n')
    (vroot / "c-extras.rsc").write_text(':global c "3"\n')
    (vroot / "README.md").write_text("ignored\n")  # not *.rsc

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "10-foo.rsc").write_text("/system/clock\n    set time-zone-name=UTC\n")

    files = load_profile(profile, vars_dir=vroot)
    names = [p.name for p in files]
    assert names == ["a-secrets.rsc", "b-vars.rsc", "c-extras.rsc", "10-foo.rsc"]


def test_load_profile_empty_vars_dir_is_ok(tmp_path: Path) -> None:
    """A vars folder with no ``*.rsc`` contributes nothing and doesn't error."""
    vroot = tmp_path / "vars"
    vroot.mkdir()
    (vroot / "notes.txt").write_text("ignored\n")

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "10-foo.rsc").write_text("/system/clock\n    set time-zone-name=UTC\n")

    files = load_profile(profile, vars_dir=vroot)
    assert [p.name for p in files] == ["10-foo.rsc"]


def test_load_profile_vars_dir_skips_subdirectories(tmp_path: Path) -> None:
    vroot = tmp_path / "vars"
    vroot.mkdir()
    (vroot / "secrets.rsc").write_text(':global a "1"\n')
    sub = vroot / "nested"
    sub.mkdir()
    (sub / "deep.rsc").write_text(':global b "2"\n')

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "10-foo.rsc").write_text("/system/clock\n    set time-zone-name=UTC\n")

    files = load_profile(profile, vars_dir=vroot)
    assert [p.name for p in files] == ["secrets.rsc", "10-foo.rsc"]


def test_load_profile_missing_dir_raises() -> None:
    with pytest.raises(LoaderError):
        load_profile(FIX / "does-not-exist")


def test_load_profile_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(LoaderError):
        load_profile(tmp_path)


def test_load_profile_missing_vars_dir_raises(tmp_path: Path) -> None:
    (tmp_path / "10-foo.rsc").write_text("/system/clock\n    set time-zone-name=UTC\n")
    with pytest.raises(LoaderError, match="vars folder not found"):
        load_profile(tmp_path, vars_dir=tmp_path / "no-such-vars")


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


def test_concat_preserves_file_order_and_content() -> None:
    files = load_profile(PROFILE, vars_dir=VARS)
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
