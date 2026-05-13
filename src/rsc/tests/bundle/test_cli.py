"""CLI smoke tests for ``rsc bundle``.

Drives :func:`rsc.bundle.cli.main` directly with arg lists and verifies
output paths, exit codes, and the two output modes (default pipeline and
``--no-flatten``). Also covers ``--vars`` discovery.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.bundle.cli import main as bundle_main  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PROFILE = FIX / "profile"
VARS = FIX  # fixtures/ holds secrets.rsc + vars.rsc


def _base_args(out: Path | None = None) -> list[str]:
    """Standard arg list: profile + explicit vars folder (+ optional -o)."""
    args = ["--profile", str(PROFILE), "--vars", str(VARS)]
    if out is not None:
        args += ["-o", str(out)]
    return args


# --- successful runs --------------------------------------------------------


def test_writes_to_explicit_file_path(tmp_path: Path) -> None:
    out = tmp_path / "bundle.rsc"
    rc = bundle_main(_base_args(out))
    assert rc == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    # Default pipeline ran: vars resolved, no `:global` lines left.
    assert ":global" not in text
    # Some recognisable content from the profile fixture survived.
    assert "/interface/list" in text or "/system/identity" in text


def test_writes_to_existing_directory_with_auto_name(tmp_path: Path) -> None:
    """``-o <existing-dir>`` -> ``<dir>/<profile>-<yymmdd>-<secs>.rsc``."""
    out_dir = tmp_path / "builds"
    out_dir.mkdir()
    rc = bundle_main(_base_args(out_dir))
    assert rc == 0
    files = list(out_dir.glob("profile-*.rsc"))
    assert len(files) == 1, list(out_dir.iterdir())
    assert re.match(r"profile-\d{6}-\d+\.rsc", files[0].name)


def test_bare_name_treated_as_directory(tmp_path: Path) -> None:
    """``-o builds`` with no existing file -> create dir, auto-name inside."""
    out_dir = tmp_path / "newdir"
    rc = bundle_main(_base_args(out_dir))
    assert rc == 0
    assert out_dir.is_dir()
    assert any(out_dir.glob("profile-*.rsc"))


def test_default_out_is_dot_out_under_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``-o`` -> ``./out/<profile>-<stamp>.rsc`` under cwd."""
    monkeypatch.chdir(tmp_path)
    rc = bundle_main(_base_args())
    assert rc == 0
    out_dir = tmp_path / "out"
    assert out_dir.is_dir()
    assert any(out_dir.glob("profile-*.rsc"))


def test_no_flatten_keeps_globals_and_banners(tmp_path: Path) -> None:
    out = tmp_path / "raw.rsc"
    rc = bundle_main(_base_args(out) + ["--no-flatten"])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    # Raw concat keeps :global lines (no flatten pass) and per-file banners.
    assert ":global" in text
    assert "begin secrets.rsc" in text


def test_short_o_flag_alias(tmp_path: Path) -> None:
    """``-o`` is the short alias for ``--out``."""
    out = tmp_path / "bundle.rsc"
    rc = bundle_main(_base_args(out))
    assert rc == 0
    assert out.is_file()


def test_prints_output_path_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI prints the resolved output path so chained scripts can capture it."""
    out = tmp_path / "bundle.rsc"
    bundle_main(_base_args(out))
    printed = capsys.readouterr().out.strip()
    # Compare via Path so backslash/forward-slash differences don't matter.
    assert Path(printed) == out


# --- vars discovery ---------------------------------------------------------


def test_default_vars_dir_is_profile_parent(tmp_path: Path) -> None:
    """When --vars is omitted, every *.rsc in <profile-parent> loads.

    Mirrors the production layout: rsc/{secrets,vars}.rsc + rsc/<profile>/.
    """
    repo = tmp_path / "repo"
    profile = repo / "myprofile"
    profile.mkdir(parents=True)
    shutil.copy(VARS / "secrets.rsc", repo / "secrets.rsc")
    shutil.copy(VARS / "vars.rsc", repo / "vars.rsc")
    for src in PROFILE.iterdir():
        shutil.copy(src, profile / src.name)

    out = tmp_path / "bundle.rsc"
    rc = bundle_main(["--profile", str(profile), "-o", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    # Variables resolved -> the fixture's $routerName / $adminPass landed.
    assert "set name=TestRouter" in text
    assert "password=secret-pw" in text


def test_default_vars_dir_with_no_globals_still_bundles(tmp_path: Path) -> None:
    """If <profile-parent> has no *.rsc, the bundle still produces output
    (just without any :global substitution)."""
    repo = tmp_path / "repo"
    profile = repo / "myprofile"
    profile.mkdir(parents=True)
    # Profile module that doesn't reference any $vars.
    (profile / "10-clock.rsc").write_text(
        "/system/clock\n    set time-zone-name=UTC\n",
    )

    out = tmp_path / "bundle.rsc"
    rc = bundle_main(["--profile", str(profile), "-o", str(out)])
    assert rc == 0
    assert "time-zone-name=UTC" in out.read_text(encoding="utf-8")


def test_explicit_vars_dir_overrides_default(tmp_path: Path) -> None:
    """``--vars`` wins over the parent-dir default."""
    repo = tmp_path / "repo"
    profile = repo / "myprofile"
    profile.mkdir(parents=True)
    # Parent has a vars file we should NOT load.
    (repo / "vars.rsc").write_text(':global routerName "WrongName"\n')
    (profile / "10-id.rsc").write_text(
        ":global routerName\n/system/identity\n    set name=$routerName\n",
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "vars.rsc").write_text(':global routerName "RightName"\n')

    out = tmp_path / "bundle.rsc"
    rc = bundle_main([
        "--profile", str(profile),
        "--vars", str(elsewhere),
        "-o", str(out),
    ])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "set name=RightName" in text
    assert "WrongName" not in text


def test_missing_explicit_vars_dir_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A bogus --vars path is reported by the loader as exit 2."""
    rc = bundle_main([
        "--profile", str(PROFILE),
        "--vars", str(tmp_path / "no-such-vars"),
        "-o", str(tmp_path / "out.rsc"),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "vars folder not found" in err


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


# --- --yaml mode ------------------------------------------------------------

YAML_PROFILE = FIX / "yaml-profile"
YAML_VARS = FIX / "yaml-vars"


def test_yaml_mode_renders_via_rsc_yaml(tmp_path: Path) -> None:
    """``--yaml`` loads .yaml files and runs them through rsc.yaml first."""
    out = tmp_path / "bundle.rsc"
    rc = bundle_main([
        "--profile", str(YAML_PROFILE),
        "--vars", str(YAML_VARS),
        "--yaml",
        "-o", str(out),
    ])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    # Default pipeline ran: vars resolved, no `:global` lines left.
    assert ":global" not in text
    # Variable substitution worked: $routerName -> TestRouter.
    assert "set name=TestRouter" in text
    # `set [find ...]` selectors round-tripped through the YAML form.
    assert "set [find name=admin]" in text
    # `/ip/service` rows kept the bare `set <name>` form.
    assert "set winbox" in text
    assert "set ssh" in text


def test_yaml_mode_matches_rsc_mode_output(tmp_path: Path) -> None:
    """The YAML and .rsc fixture profiles describe the same config; the
    bundler output must be structurally equivalent (same items per menu,
    same identity keys, same property values) even though the literal
    column order may differ -- the .rsc fixture and the YAML fixture
    happen to emit `name=` and `comment=` in different order, and that
    order is preserved through compact emit."""
    from rsc.parser import parse_text  # local import: keeps top of file lean

    rsc_out = tmp_path / "from-rsc.rsc"
    yaml_out = tmp_path / "from-yaml.rsc"

    bundle_main(_base_args(rsc_out))
    bundle_main([
        "--profile", str(YAML_PROFILE),
        "--vars", str(YAML_VARS),
        "--yaml",
        "-o", str(yaml_out),
    ])

    rsc_cfg = parse_text(rsc_out.read_text(encoding="utf-8"))
    yaml_cfg = parse_text(yaml_out.read_text(encoding="utf-8"))

    # Same set of menus, in the same order.
    assert list(rsc_cfg.items_by_menu) == list(yaml_cfg.items_by_menu)
    # Same items per menu (compared as prop dicts -- key order in the
    # underlying mapping doesn't matter for semantics).
    for menu in rsc_cfg.items_by_menu:
        rsc_items = rsc_cfg.items_by_menu[menu]
        yaml_items = yaml_cfg.items_by_menu[menu]
        assert len(rsc_items) == len(yaml_items), menu
        for r, y in zip(rsc_items, yaml_items):
            assert r.verb == y.verb
            assert dict(r.props) == dict(y.props), (menu, r.props, y.props)


def test_yaml_mode_no_flatten_keeps_globals(tmp_path: Path) -> None:
    out = tmp_path / "raw.rsc"
    rc = bundle_main([
        "--profile", str(YAML_PROFILE),
        "--vars", str(YAML_VARS),
        "--yaml",
        "-o", str(out),
        "--no-flatten",
    ])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    # Raw concat keeps :global lines (rendered from `globals:` entries).
    assert ":global adminPass" in text
    # Banners use the .rsc-suffix synthetic name.
    assert "begin secrets.rsc" in text
    assert "begin vars.rsc" in text


def test_yaml_mode_empty_profile_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """``LoaderError`` for ``no .yaml files`` (vs ``no .rsc files``)."""
    empty = tmp_path / "empty-yaml-profile"
    empty.mkdir()
    rc = bundle_main(["--profile", str(empty), "--yaml"])
    assert rc == 2
    err = capsys.readouterr().err
    assert ".yaml" in err


def test_yaml_mode_malformed_yaml_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A YamlError from the converter surfaces as exit 2 with the file path."""
    profile = tmp_path / "broken"
    profile.mkdir()
    (profile / "10-bad.yaml").write_text(
        "interface:\n  list:\n    - name: oops-no-operation\n",
        encoding="utf-8",
    )
    rc = bundle_main([
        "--profile", str(profile),
        "--yaml",
        "-o", str(tmp_path / "out.rsc"),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "10-bad.yaml" in err
    assert "operation" in err
