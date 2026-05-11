"""Tests for the compact emitter + end-to-end bundle() pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.parser import Config, Item, parse_text  # noqa: E402

from rsc.bundle import bundle  # noqa: E402
from rsc.bundle.compact import emit  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "profile"


# --- compact.emit unit tests ------------------------------------------------


def test_emit_one_line_per_op() -> None:
    cfg = Config()
    cfg.add(Item(menu="/interface/list", verb="add",
                 props={"name": "iac.list.wan"}))
    cfg.add(Item(menu="/interface/list", verb="add",
                 props={"name": "iac.list.lan"}))
    out = emit(cfg)
    lines = out.strip().splitlines()
    assert lines[0] == "/interface/list"
    assert lines[1] == "add name=iac.list.wan"
    assert lines[2] == "add name=iac.list.lan"
    # No backslash-continuations, no blank lines, no leading indent.
    assert "\\" not in out
    assert "    " not in out
    assert "\n\n" not in out


def test_emit_preserves_iac_comment_verbatim() -> None:
    """comment= is preserved as authored. The bundle stays a faithful
    representation of the source so rsc.diff against /export gets a
    clean delta."""
    cfg = Config()
    cfg.add(Item(menu="/ip/dhcp-server/lease", verb="add", props={
        "address": "192.168.10.5",
        "mac-address": "64:49:7D:D4:86:54",
        "comment": '"iac.lease.int.5 -- SAW"',
    }))
    out = emit(cfg)
    assert 'comment="iac.lease.int.5 -- SAW"' in out


def test_emit_preserves_non_iac_comment() -> None:
    """Comments without an iac token must NOT be dropped (a previous
    minify-default behaviour silently lost them)."""
    cfg = Config()
    cfg.add(Item(menu="/user", verb="set", props={
        "__selector__": "[find name=admin]",
        "password": "secret",
        "comment": '"Default admin"',
    }))
    out = emit(cfg)
    assert 'comment="Default admin"' in out
    assert "set [find name=admin]" in out
    assert "password=secret" in out


def test_emit_preserves_set_selector() -> None:
    cfg = Config()
    cfg.add(Item(menu="/interface/ethernet", verb="set", props={
        "__selector__": "[find default-name=ether1]",
        "name": "iac.ether.wan",
    }))
    out = emit(cfg)
    assert "set [find default-name=ether1] name=iac.ether.wan" in out


def test_emit_skips_redundant_selector_kv_prop() -> None:
    """When the parser surfaces ``KEY=VAL`` from ``[find KEY=VAL]`` into
    props, the emitter must not re-emit it: ``set [find default=yes]
    default=yes ...`` would round-trip as a phantom prop and the differ
    would see drift between candidate and live (regression: ``/ipv6/nd``).
    """
    cfg = Config()
    cfg.add(Item(menu="/ipv6/nd", verb="set", props={
        "__selector__": "[find default=yes]",
        "default": "yes",                 # surfaced by parser from selector
        "advertise-dns": "yes",
    }))
    out = emit(cfg).strip()
    # Selector kept; surfaced KEY=VAL not re-emitted; non-selector props kept.
    assert "set [find default=yes] advertise-dns=yes" in out, out
    assert "default=yes advertise-dns=yes" not in out, out


def test_emit_keeps_kv_prop_when_value_differs_from_selector() -> None:
    """Sanity: only the EXACT selector key+value pair is suppressed.

    If for some reason the prop carries a different value than the
    selector, the emitter must still emit it (the user is overriding).
    """
    cfg = Config()
    cfg.add(Item(menu="/ipv6/nd", verb="set", props={
        "__selector__": "[find default=yes]",
        "default": "no",  # different value -> must be emitted
        "advertise-dns": "yes",
    }))
    out = emit(cfg)
    assert "set [find default=yes] default=no advertise-dns=yes" in out, out


def test_emit_quotes_values_with_spaces() -> None:
    cfg = Config()
    cfg.add(Item(menu="/system/identity", verb="set", props={
        "name": "Some Router",
    }))
    out = emit(cfg)
    assert 'set name="Some Router"' in out


def test_emit_keeps_empty_quoted_value() -> None:
    cfg = Config()
    cfg.add(Item(menu="/system/routerboard/wps-button", verb="set", props={
        "on-event": "",
    }))
    out = emit(cfg)
    # Empty string must keep the explicit quotes -- bare `=` would parse
    # as a missing value at /import time.
    assert 'on-event=""' in out


def test_emit_passes_bracket_expression_through_unquoted() -> None:
    """`admin-mac=[expr]` must not gain quotes -- that would turn the
    script-resolved expression into a literal string at /import time."""
    cfg = Config()
    cfg.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan",
        "admin-mac":
            "[/interface/ethernet get [find name=iac.ether.lan1] mac-address]",
    }))
    out = emit(cfg)
    assert (
        "admin-mac=[/interface/ethernet get "
        "[find name=iac.ether.lan1] mac-address]"
    ) in out
    # No quotes around the bracket value.
    assert 'admin-mac="[' not in out


def test_emit_skips_empty_menu() -> None:
    cfg = Config()
    cfg.items_by_menu["/never-populated"] = []
    cfg.add(Item(menu="/system/clock", verb="set",
                 props={"time-zone-name": "UTC"}))
    out = emit(cfg)
    assert "/never-populated" not in out
    assert "/system/clock" in out


# --- end-to-end bundle() ----------------------------------------------------


def test_bundle_substitutes_vars_and_preserves_comments() -> None:
    out = bundle(str(FIX))
    # Variable substitution: $adminCidrs -> "192.168.10.2,192.168.10.3"
    # The comma-separated value has no whitespace, so it stays bare.
    assert "address=192.168.10.2,192.168.10.3" in out
    assert "$adminCidrs" not in out
    # $routerName -> "TestRouter" (bare)
    assert "set name=TestRouter" in out
    # $adminPass -> "secret-pw"
    assert "password=secret-pw" in out
    # Comments preserved verbatim (with their iac id token + description).
    assert 'comment="iac.list.wan -- WAN uplink"' in out
    assert "WAN uplink" in out
    # Default admin comment also survives.
    assert "Default admin" in out
    # No script wrappers leaked through.
    assert ":global" not in out
    assert ":local" not in out


def test_bundle_preserves_identity_for_diffability() -> None:
    """Bundle output must round-trip through parse_text + give every item
    an identity_key the differ can use."""
    out = bundle(str(FIX))
    cfg = parse_text(out)

    # /interface/list -> name= identity
    list_idx = cfg.index("/interface/list")
    assert "name=iac.list.wan" in list_idx
    assert "name=iac.list.lan" in list_idx

    # /interface/ethernet -> default-name= identity (the set [find ...] row)
    eth = cfg.items_by_menu["/interface/ethernet"][0]
    assert eth.props["__selector__"] == "[find default-name=ether1]"


def test_bundle_no_flatten_keeps_globals() -> None:
    out = bundle(str(FIX), flatten_output=False)
    # Raw concat: :global lines and $var refs survive untouched.
    assert ':global adminPass    "secret-pw"' in out
    assert "$adminCidrs" in out
    # Banner markers survive too.
    assert "# >>> begin secrets.rsc" in out


def test_bundle_output_smaller_than_source() -> None:
    """Compact output should be materially smaller than the raw concat
    (banners + scripting glue + line wrapping all gone)."""
    raw = bundle(str(FIX), flatten_output=False)
    minimized = bundle(str(FIX))
    assert len(minimized) < len(raw)
    # No backslash continuations in the minimized form.
    assert "\\\n" not in minimized
