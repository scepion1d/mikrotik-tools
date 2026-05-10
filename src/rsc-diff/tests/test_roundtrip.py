"""Smoke tests: parse fixtures, diff, ensure expected ops appear."""

from __future__ import annotations

import sys
from pathlib import Path

# Make `rsc_diff` importable when running this file directly with `python`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsc_diff import diff, emit, parse_file  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def test_parse_empty() -> None:
    cfg = parse_file(FIX / "empty.rsc")
    assert cfg.menus() == [], cfg.menus()


def test_parse_minimal() -> None:
    cfg = parse_file(FIX / "minimal_a.rsc")
    menus = set(cfg.menus())
    assert "/interface/list" in menus
    assert "/ip/dns" in menus
    assert "/ip/firewall/filter" in menus
    assert "/ip/dhcp-server/lease" in menus

    list_items = cfg.items_by_menu["/interface/list"]
    assert len(list_items) == 2
    assert list_items[0].props.get("name") == "iac.list.wan"


def test_diff_a_to_b_emits_expected_ops() -> None:
    a = parse_file(FIX / "minimal_a.rsc")
    b = parse_file(FIX / "minimal_b.rsc")
    ops = diff(a, b)

    by_kind = {"add": 0, "set": 0, "remove": 0, "wipe": 0}
    for op in ops:
        by_kind[op.kind] += 1

    # Expected from a -> b:
    #   - add iac.list.mgmt
    #   - set iac.list.wan (comment changed)
    #   - set /ip/dns (servers added)
    #   - add iac.fw.in.2 (icmp rule)
    #   - remove iac.lease.usbeth
    #   - add iac.lease.surface
    assert by_kind["add"] >= 3, ops
    assert by_kind["set"] >= 2, ops
    assert by_kind["remove"] >= 1, ops

    rendered = emit(ops)
    assert "iac.list.mgmt" in rendered
    assert "iac.fw.in.2" in rendered
    assert "iac.lease.usbeth" in rendered  # in remove clause
    assert "iac.lease.surface" in rendered


def test_diff_self_is_empty() -> None:
    a = parse_file(FIX / "minimal_a.rsc")
    b = parse_file(FIX / "minimal_a.rsc")
    assert diff(a, b) == []


def test_ordered_menu_emits_wipe_then_add() -> None:
    """Any non-trivial change in an ordered menu becomes wipe-then-add."""
    a = parse_file(FIX / "minimal_a.rsc")
    b = parse_file(FIX / "minimal_b.rsc")
    ops = diff(a, b)

    fw_ops = [op for op in ops if op.menu == "/ip/firewall/filter"]
    assert fw_ops, "expected /ip/firewall/filter ops"
    assert fw_ops[0].kind == "wipe", fw_ops[0]
    # Every following op in this menu is an `add` -- no per-rule set/remove.
    assert all(op.kind == "add" for op in fw_ops[1:]), fw_ops

    rendered = emit(ops)
    # Renderer brackets the wipe-then-add block with section comments.
    assert "# 1. REMOVE OLD" in rendered
    assert "# 2. ADD NEW" in rendered
    # Wipe uses `dynamic=no` so RouterOS's built-in defconf rules survive.
    assert "remove [find dynamic=no]" in rendered


def test_ordered_menu_unchanged_emits_nothing() -> None:
    """If an ordered menu is identical between configs, no ops emitted."""
    a = parse_file(FIX / "minimal_a.rsc")
    b = parse_file(FIX / "minimal_a.rsc")
    ops = diff(a, b)
    assert all(op.menu != "/ip/firewall/filter" for op in ops), ops


def test_removed_string_property_emits_reset() -> None:
    """Property in old, missing in new, non-boolean -> emit `reset prop`."""
    from rsc_diff import Config, Item, diff, emit

    old = Config()
    old.add(Item(menu="/interface/bridge", verb="add",
                 props={"name": "iac.b", "vlan-filtering": "yes",
                        "comment": "x"}))
    new = Config()
    new.add(Item(menu="/interface/bridge", verb="add",
                 props={"name": "iac.b"}))

    ops = diff(old, new)
    kinds = {op.kind for op in ops}
    # `vlan-filtering=yes` -> boolean, becomes set vlan-filtering=no
    # `comment="x"` -> non-boolean, becomes reset comment
    assert "set" in kinds, ops
    assert "reset" in kinds, ops

    rendered = emit(ops)
    assert "vlan-filtering=no" in rendered, rendered
    assert "reset" in rendered and "comment" in rendered, rendered


def test_removed_boolean_uses_set_no_not_reset() -> None:
    """Removed boolean folds into a `set prop=no`, not into a `reset`."""
    from rsc_diff import Config, Item, diff, emit

    old = Config()
    old.add(Item(menu="/interface/bridge", verb="add",
                 props={"name": "iac.b", "vlan-filtering": "yes"}))
    new = Config()
    new.add(Item(menu="/interface/bridge", verb="add",
                 props={"name": "iac.b"}))

    ops = diff(old, new)
    rendered = emit(ops)
    assert "vlan-filtering=no" in rendered
    assert "reset" not in rendered, "boolean removal should not produce reset"


def test_positional_removes_emit_descending() -> None:
    """Removing positional items must emit highest index first.

    Otherwise the second @anon=N selector targets a shifted row at apply
    time. Verifies the regression we hit in the rollback diff for
    /interface/bridge/vlan and /interface/list/member.
    """
    from rsc_diff import Config, Item, diff

    # Three anonymous items in a non-name, non-comment menu (using a menu
    # not in MENUS_WITH_NAME / MENUS_ORDERED so identity falls to @anon=N).
    old = Config()
    for i in range(3):
        old.add(Item(menu="/interface/bridge/port", verb="add",
                     props={"interface": f"iac.ether.lan{i}"}))
    new = Config()  # all three removed

    ops = diff(old, new)
    removes = [op for op in ops if op.kind == "remove"]
    assert len(removes) == 3, ops
    # IDs should descend: @anon=2 first, then @anon=1, then @anon=0.
    keys = [op.identity_key for op in removes]
    assert keys == ["@anon=2", "@anon=1", "@anon=0"], keys


def test_default_value_treated_as_absent() -> None:
    """A property with the documented default value matches an absent prop."""
    from rsc_diff import Config, Item, diff

    # /interface/bridge protocol-mode default is "rstp" (in defaults table).
    src = Config()
    src.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan", "protocol-mode": "rstp",
    }))
    router = Config()
    router.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan",  # protocol-mode omitted -> at default
    }))

    # No drift expected.
    assert diff(src, router) == []
    # Reverse direction: also no drift.
    assert diff(router, src) == []


def test_default_normalisation_off_under_strict() -> None:
    """--strict disables defaults; explicit-vs-absent shows as drift."""
    from rsc_diff import Config, Item, diff

    src = Config()
    src.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan", "protocol-mode": "rstp",
    }))
    router = Config()
    router.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan",
    }))

    ops = diff(src, router, strict=True)
    assert ops, "strict mode should NOT collapse default vs absent"
    # In strict mode this becomes a `reset protocol-mode` op.
    assert any(op.kind == "reset" for op in ops), ops


def test_real_drift_is_never_silenced_by_defaults() -> None:
    """Critical safety test: a prop value DIFFERENT from default is visible.

    If a default-table entry is wrong, this test catches the false-erasure
    pattern: source says X, router says Y, and Y happens to equal what
    we *think* is the default. The differ MUST emit a `set` op.
    """
    from rsc_diff import Config, Item, diff

    # Source explicitly wants vlan-filtering=no.
    src = Config()
    src.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan", "vlan-filtering": "no",
    }))
    # Router has it on (drift).
    router = Config()
    router.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan", "vlan-filtering": "yes",
    }))

    # We DO want a diff here regardless of any "vlan-filtering=no is default" entry.
    ops = diff(src, router)
    assert ops, "real drift must never be silenced"
    assert any(
        op.kind == "set" and op.props.get("vlan-filtering") == "yes"
        for op in ops
    ), ops


def test_computed_property_dropped_both_sides() -> None:
    """Computed props (e.g. /ip/address network) never appear in ops."""
    from rsc_diff import Config, Item, diff

    src = Config()
    src.add(Item(menu="/ip/address", verb="add", props={
        "address": "192.168.10.1/24",
        "interface": "iac.bridge.lan",
    }))
    router = Config()
    router.add(Item(menu="/ip/address", verb="add", props={
        "address": "192.168.10.1/24",
        "interface": "iac.bridge.lan",
        "network": "192.168.10.0",  # router auto-derived
    }))

    ops = diff(src, router)
    assert all("network" not in op.props for op in ops), ops


def test_set_with_find_emits_set_when_live_omits_row() -> None:
    """Built-in rows like /user admin are omitted from /export when no
    property differs from default. The candidate's `set [find name=admin]`
    must still emit `set [find name=admin] ...`, not `add ...` (which
    would create a second admin row -- broken)."""
    from rsc_diff import parse_text, diff, emit

    live = parse_text("")  # no /user rows at all
    candidate = parse_text(
        "/user\n"
        "    set [find name=admin] password=secret\n"
    )

    ops = diff(live, candidate)
    assert len(ops) == 1, ops
    assert ops[0].kind == "set", ops
    assert ops[0].menu == "/user", ops
    rendered = emit(ops)
    assert "set [find name=admin] password=secret" in rendered, rendered
    # Critically: NO `add password=...` (would create a second user).
    assert "add password" not in rendered, rendered


def test_user_admin_is_never_removed() -> None:
    """The admin row is built-in: removing it would lock the operator out.
    A candidate that omits /user must NOT cause `remove [find name=admin]`."""
    from rsc_diff import parse_text, diff

    live = parse_text(
        "/user\n"
        "    set [find name=admin] comment=\"old admin\"\n"
    )
    candidate = parse_text("")  # candidate has no /user menu at all

    ops = diff(live, candidate)
    # No remove op against /user; the admin row is protected.
    assert all(
        not (op.kind == "remove" and op.menu == "/user")
        for op in ops
    ), ops


def test_wifi_mac_address_is_computed() -> None:
    """`/interface/wifi mac-address` is auto-derived for virtual APs.
    The differ must NOT emit `reset mac-address` when the candidate is
    silent -- doing so would kick paired clients off the SSID."""
    from rsc_diff import Config, Item, diff

    live = Config()
    live.add(Item(menu="/interface/wifi", verb="add", props={
        "name": "iac.wifi.iot.2g",
        "master-interface": "iac.wifi.int.2g",
        "mac-address": "D2:EA:11:40:F0:06",  # router auto-derived
    }))
    candidate = Config()
    candidate.add(Item(menu="/interface/wifi", verb="add", props={
        "name": "iac.wifi.iot.2g",
        "master-interface": "iac.wifi.int.2g",
        # no mac-address -- authored side never sets it
    }))

    ops = diff(live, candidate)
    # No reset / set on mac-address.
    assert all(
        "mac-address" not in op.props for op in ops
    ), ops


def test_bridge_admin_mac_is_computed() -> None:
    """`/interface/bridge admin-mac` is normally authored as a bracket
    expression that RouterOS resolves at import time. The literal MAC
    that ends up in /export should not look like drift against the
    authored bracket form."""
    from rsc_diff import Config, Item, diff

    live = Config()
    live.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan",
        "admin-mac": "D0:EA:11:40:F0:01",  # resolved by router at import
    }))
    candidate = Config()
    candidate.add(Item(menu="/interface/bridge", verb="add", props={
        "name": "iac.bridge.lan",
        "admin-mac": "[/interface/ethernet get [find name=iac.ether.lan1] mac-address]",
    }))

    ops = diff(live, candidate)
    assert all(
        "admin-mac" not in op.props for op in ops
    ), ops


if __name__ == "__main__":
    test_parse_empty()
    test_parse_minimal()
    test_diff_a_to_b_emits_expected_ops()
    test_diff_self_is_empty()
    test_ordered_menu_emits_wipe_then_add()
    test_ordered_menu_unchanged_emits_nothing()
    test_removed_string_property_emits_reset()
    test_removed_boolean_uses_set_no_not_reset()
    test_positional_removes_emit_descending()
    test_default_value_treated_as_absent()
    test_default_normalisation_off_under_strict()
    test_real_drift_is_never_silenced_by_defaults()
    test_computed_property_dropped_both_sides()
    print("ok")
