"""Tests for :mod:`rsc.schema` (library) and :mod:`rsc.schema.cli` (CLI).

Two surfaces under test:

- :func:`rsc.schema.bundle` / :func:`rsc.schema.render` -- merge the
  in-package JSON fragments into one schema document.
- :func:`rsc.schema.cli.main` -- the ``rsc schema`` subcommand: write
  to stdout, write to a path, or ``--check`` against an on-disk file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc import schema as schema_mod  # noqa: E402
from rsc.schema import bundle, render  # noqa: E402
from rsc.schema.cli import main as schema_main  # noqa: E402


# --- library: bundle() ------------------------------------------------------


def test_bundle_returns_draft7_schema_with_definitions() -> None:
    """`bundle()` returns a Draft-07 schema dict with merged definitions."""
    doc = bundle()
    assert isinstance(doc, dict)
    assert doc.get("$schema") == "http://json-schema.org/draft-07/schema#"
    defs = doc.get("definitions")
    assert isinstance(defs, dict) and defs, "definitions must be populated"


def test_bundle_merges_well_known_definitions() -> None:
    """Spot-check definitions from each fragment type are present.

    Catches a regression where a menu fragment stops shipping in the
    wheel or the merge order drops keys.
    """
    defs = bundle()["definitions"]
    # `_common.json` primitives
    assert "iacToken" in defs
    assert "ipv4Cidr" in defs
    # menu_*.json shape definitions
    for name in (
        "menu_interface",
        "menu_ip",
        "menu_ipv6",
        "menu_system",
        "menu_user",
        "menu_disk",
        "menu_tool",
        "menu_generic",
    ):
        assert name in defs, f"missing menu definition: {name}"
    # item_* row schemas
    assert "item_interface_list" in defs


def test_bundle_is_deterministic() -> None:
    """Two calls must produce structurally identical dicts."""
    assert bundle() == bundle()


def test_bundle_preserves_root_top_level_keys() -> None:
    """Root metadata (`$id`, `description`, top-level shape) survives the merge."""
    doc = bundle()
    # `$id` lives in _root.json; merge must not strip it.
    assert "$id" in doc
    # The bundled schema should remain useful as a JSON-Schema-of-everything;
    # at minimum it should describe an object (`type: object` or `oneOf`).
    assert "oneOf" in doc or doc.get("type") == "object"


def test_bundle_raises_on_duplicate_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate keys across fragments are always a bug -- fail loudly."""
    # Copy every real fragment into tmp_path and add a duplicate.
    src_dir: Path = schema_mod._HERE
    for f in src_dir.glob("*.json"):
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    # Pick a known existing key from _common.json and re-define it.
    extra = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "iacToken": {"type": "string", "description": "duplicate -- should trip"},
        },
    }
    (tmp_path / "menu_zz_duplicate.json").write_text(
        json.dumps(extra), encoding="utf-8",
    )
    monkeypatch.setattr(schema_mod, "_HERE", tmp_path)

    with pytest.raises(ValueError, match="duplicate definition `iacToken`"):
        bundle()


def test_bundle_raises_when_root_fragment_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schema dir without ``_root.json`` is an unrecoverable error."""
    monkeypatch.setattr(schema_mod, "_HERE", tmp_path)
    with pytest.raises(FileNotFoundError, match="_root.json"):
        bundle()


def test_bundle_raises_on_malformed_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JSON-decode failure in any fragment surfaces as RuntimeError."""
    src_dir: Path = schema_mod._HERE
    # Ship a valid _root.json plus one broken fragment.
    (tmp_path / "_root.json").write_text(
        (src_dir / "_root.json").read_text(encoding="utf-8"), encoding="utf-8",
    )
    (tmp_path / "menu_broken.json").write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(schema_mod, "_HERE", tmp_path)

    with pytest.raises(RuntimeError, match="cannot load schema fragment"):
        bundle()


# --- library: _ordered_fragments() ------------------------------------------


def test_ordered_fragments_root_first_then_common_then_alphabetical() -> None:
    """Merge order matters: root + common before any menu_*."""
    paths = schema_mod._ordered_fragments()
    names = [p.name for p in paths]
    assert names[0] == "_root.json"
    if "_common.json" in names:
        assert names[1] == "_common.json"
        tail = names[2:]
    else:
        tail = names[1:]
    # All `menu_*` files (and anything else) come back in alphabetical order.
    assert tail == sorted(tail)


# --- library: render() ------------------------------------------------------


def test_render_is_valid_json_round_tripping_bundle() -> None:
    """`render()` text parses back to the same dict `bundle()` returns."""
    text = render()
    assert json.loads(text) == bundle()


def test_render_uses_stable_formatting() -> None:
    """2-space indent + single trailing newline => diff-friendly output."""
    text = render()
    assert text.endswith("\n")
    # No CR (Windows newline) should leak in -- writers use newline="\n".
    assert "\r" not in text
    # First line is `{`; second line is indented with two spaces (object body).
    lines = text.split("\n")
    assert lines[0] == "{"
    assert lines[1].startswith("  ") and not lines[1].startswith("   ")


def test_render_is_byte_stable_across_calls() -> None:
    """Two `render()` calls must yield byte-identical strings."""
    assert render() == render()


# --- CLI: stdout / --out ----------------------------------------------------


def test_cli_no_out_writes_bundle_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = schema_main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == render()
    assert json.loads(out) == bundle()


def test_cli_out_writes_file_and_creates_parent_dirs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    out_path = tmp_path / "nested" / "more" / "schema.json"
    rc = schema_main(["--out", str(out_path)])
    assert rc == 0
    assert out_path.is_file()
    assert out_path.read_text(encoding="utf-8") == render()
    out = capsys.readouterr().out
    assert "wrote" in out
    assert str(out_path) in out


def test_cli_out_writes_lf_line_endings_on_all_platforms(tmp_path: Path) -> None:
    """The writer must force LF so the file diffs cleanly on Windows."""
    out_path = tmp_path / "schema.json"
    schema_main(["--out", str(out_path)])
    # read_bytes (not read_text) to bypass universal-newline translation.
    raw = out_path.read_bytes()
    assert b"\r\n" not in raw, "writer must use LF, not CRLF"
    assert raw.endswith(b"\n")


# --- CLI: --check -----------------------------------------------------------


def test_cli_check_requires_out(capsys: pytest.CaptureFixture[str]) -> None:
    rc = schema_main(["--check"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--check requires --out" in err


def test_cli_check_passes_when_file_in_sync(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    out_path = tmp_path / "schema.json"
    # Seed the file with the current bundle, then --check.
    assert schema_main(["--out", str(out_path)]) == 0
    capsys.readouterr()  # drop the `wrote ...` line

    rc = schema_main(["--out", str(out_path), "--check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "in sync" in out


def test_cli_check_fails_on_stale_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    out_path = tmp_path / "schema.json"
    out_path.write_text("{}\n", encoding="utf-8")

    rc = schema_main(["--out", str(out_path), "--check"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "MISMATCH" in err
    assert str(out_path) in err
    # Hint tells the user how to fix it.
    assert "rsc schema --out" in err


def test_cli_check_fails_when_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "no-such" / "schema.json"
    rc = schema_main(["--out", str(missing), "--check"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot read" in err
    assert str(missing) in err


# --- round-trip end-to-end --------------------------------------------------


def test_cli_round_trip_write_then_check(tmp_path: Path) -> None:
    """Write the bundle, then --check it -- should always agree."""
    out_path = tmp_path / "schema.json"
    assert schema_main(["--out", str(out_path)]) == 0
    assert schema_main(["--out", str(out_path), "--check"]) == 0


def test_cli_writes_match_render_byte_for_byte(tmp_path: Path) -> None:
    """Output file is exactly what `render()` produces -- no extra trailing
    whitespace, no BOM, no platform-specific encoding artefacts."""
    out_path = tmp_path / "schema.json"
    schema_main(["--out", str(out_path)])
    assert out_path.read_bytes() == render().encode("utf-8")
