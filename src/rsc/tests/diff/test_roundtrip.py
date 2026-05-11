"""Smoke tests: parse fixtures, diff, ensure expected ops appear."""

from __future__ import annotations

import sys
from pathlib import Path

# Make `rsc.diff` importable when running this file directly with `python`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.diff import diff, emit, parse_file  # noqa: E402

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
    from rsc.diff import Config, Item, diff, emit

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
    from rsc.diff import Config, Item, diff, emit

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
    from rsc.diff import Config, Item, diff

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


def test_list_member_emitted_after_vlan() -> None:
    """``/interface/list/member`` must be emitted AFTER ``/interface/vlan``.

    Membership rows reference VLAN names via ``interface=``; alphabetic
    order would put ``/interface/list/member`` before ``/interface/vlan``,
    causing "input does not match any value of interface" on a fresh
    deploy where neither side existed before.
    """
    from rsc.diff import Config, Item, diff

    old = Config()  # empty router
    new = Config()
    new.add(Item(menu="/interface/list", verb="add", props={
        "name": "iac.list.lan",
    }))
    new.add(Item(menu="/interface/vlan", verb="add", props={
        "name": "iac.vlan.ext", "interface": "iac.bridge.lan", "vlan-id": "30",
    }))
    new.add(Item(menu="/interface/list/member", verb="add", props={
        "list": "iac.list.lan", "interface": "iac.vlan.ext",
    }))

    ops = diff(old, new)
    menu_order = [op.menu for op in ops if op.kind == "add"]
    assert "/interface/vlan" in menu_order
    assert "/interface/list/member" in menu_order
    assert menu_order.index("/interface/vlan") < menu_order.index(
        "/interface/list/member"
    ), menu_order


def test_wifi_configuration_emitted_after_datapath() -> None:
    """``/interface/wifi/configuration`` must be emitted AFTER its deps.

    Configuration rows reference ``datapath=``, ``security=``, and
    ``channel=``; alphabetically ``configuration`` < ``datapath`` so
    without an explicit canonical order the configuration add fires
    first and RouterOS rejects it with "input does not match any value
    of datapath".
    """
    from rsc.diff import Config, Item, diff

    old = Config()  # empty router
    new = Config()
    new.add(Item(menu="/interface/wifi/datapath", verb="add", props={
        "name": "iac.wifi.dp.ext", "bridge": "iac.bridge.lan", "vlan-id": "30",
    }))
    new.add(Item(menu="/interface/wifi/security", verb="add", props={
        "name": "iac.wifi.sec.ext", "authentication-types": "wpa2-psk",
    }))
    new.add(Item(menu="/interface/wifi/channel", verb="add", props={
        "name": "iac.wifi.ch.2g", "band": "2ghz-ax",
    }))
    new.add(Item(menu="/interface/wifi/configuration", verb="add", props={
        "name": "iac.wifi.cfg.ext.2g", "mode": "ap",
        "channel": "iac.wifi.ch.2g",
        "security": "iac.wifi.sec.ext",
        "datapath": "iac.wifi.dp.ext",
    }))
    new.add(Item(menu="/interface/wifi", verb="add", props={
        "name": "iac.wifi.ext.2g",
        "configuration": "iac.wifi.cfg.ext.2g",
    }))

    ops = diff(old, new)
    menu_order = [op.menu for op in ops if op.kind == "add"]
    # Each prerequisite must precede the menu that references it.
    for dep in ("/interface/wifi/datapath",
                "/interface/wifi/security",
                "/interface/wifi/channel"):
        assert menu_order.index(dep) < menu_order.index(
            "/interface/wifi/configuration"
        ), (dep, menu_order)
    assert menu_order.index("/interface/wifi/configuration") < menu_order.index(
        "/interface/wifi"
    ), menu_order


def test_unknown_menu_sorts_after_canonical() -> None:
    """Menus not in MENU_ORDER are emitted after every canonical menu.

    Guards against an unrecognised menu accidentally landing in the
    middle of the canonical sequence and shifting dependents.
    """
    from rsc.diff import Config, Item, diff

    old = Config()
    new = Config()
    # /interface/vlan is in MENU_ORDER; /something/unknown is not.
    new.add(Item(menu="/interface/vlan", verb="add", props={
        "name": "iac.vlan.x", "interface": "iac.bridge.lan", "vlan-id": "99",
    }))
    new.add(Item(menu="/zzz/never-heard-of-it", verb="add", props={
        "name": "iac.x",
    }))

    ops = diff(old, new)
    menu_order = [op.menu for op in ops if op.kind == "add"]
    assert menu_order.index("/interface/vlan") < menu_order.index(
        "/zzz/never-heard-of-it"
    ), menu_order


def test_default_value_treated_as_absent() -> None:
    """A property with the documented default value matches an absent prop."""
    from rsc.diff import Config, Item, diff

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
    from rsc.diff import Config, Item, diff

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
    from rsc.diff import Config, Item, diff

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
    from rsc.diff import Config, Item, diff

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
    from rsc.diff import parse_text, diff, emit

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
    from rsc.diff import parse_text, diff

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
    from rsc.diff import Config, Item, diff

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
    from rsc.diff import Config, Item, diff

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


def test_dhcp_client_name_is_computed() -> None:
    """``/ip/dhcp-client name=clientN`` is auto-assigned by RouterOS.

    The authored config never sets ``name=`` on dhcp-client rows (the
    iac.* identity lives in ``comment=``), but /export emits it as
    ``client1`` / ``client2`` etc. Without treating it as computed, the
    differ produces phantom ``reset name`` (rollforward) and
    ``set name=clientN`` (rollback) ops on every deploy.
    """
    from rsc.diff import Config, Item, diff

    live = Config()
    live.add(Item(menu="/ip/dhcp-client", verb="add", props={
        "comment": '"iac.dhcpc.wan -- WAN DHCP client"',
        "interface": "iac.ether.wan",
        "name": "client1",          # auto-assigned by RouterOS
    }))
    candidate = Config()
    candidate.add(Item(menu="/ip/dhcp-client", verb="add", props={
        "comment": '"iac.dhcpc.wan -- WAN DHCP client"',
        "interface": "iac.ether.wan",
        # no `name=` -- never authored
    }))

    # Both directions: no drift, and `name` must never appear in any op.
    assert diff(live, candidate) == []
    assert diff(candidate, live) == []


# --- IP address text-form normalisation -------------------------------------


def test_normalise_value_canonicalises_ipv6() -> None:
    """``::ffff:0.0.0.0/96`` (export form) == ``::ffff:0:0/96`` (authored)."""
    from rsc.diff.differ import _normalise_value

    assert (
        _normalise_value("::ffff:0.0.0.0/96")
        == _normalise_value("::ffff:0:0/96")
    )
    # Address case folding: 2001:DB8::1 == 2001:db8::1.
    assert (
        _normalise_value("2001:DB8::1")
        == _normalise_value("2001:db8::1")
    )


def test_normalise_value_preserves_host_bits_in_prefix() -> None:
    """``192.168.1.5/24`` must NOT collapse to ``192.168.1.0/24``.

    On /ip/address the host bits encode the interface IP. Using
    ip_interface (not ip_network) keeps them intact while still
    canonicalising textual form.
    """
    from rsc.diff.differ import _normalise_value

    assert _normalise_value("192.168.1.5/24") == "192.168.1.5/24"


def test_normalise_value_passes_non_addresses_through() -> None:
    """Port lists, MACs, names, comments are left untouched."""
    from rsc.diff.differ import _normalise_value

    # Port list -- looks like comma-separated numbers, NOT IPs.
    assert _normalise_value("53,67") == "53,67"
    # Port range with `-` -- must not be misread as an IP range.
    assert _normalise_value("1024-2048") == "1024-2048"
    # MAC address (6 octets, not a valid IPv6).
    assert _normalise_value("D0:EA:11:40:F0:01") == "D0:EA:11:40:F0:01"
    # Plain identifier.
    assert _normalise_value("iac.list.wan") == "iac.list.wan"


def test_normalise_value_handles_lists_and_ranges() -> None:
    """Comma-separated address list and dashed IP range both canonicalise."""
    from rsc.diff.differ import _normalise_value

    # Address list mixing IPv4 and IPv6 prefixes.
    assert (
        _normalise_value("192.168.1.0/24,::ffff:0.0.0.0/96")
        == _normalise_value("192.168.1.0/24,::ffff:0:0/96")
    )
    # IPv4 range -- text already canonical, must round-trip unchanged.
    assert (
        _normalise_value("192.168.20.250-192.168.20.254")
        == "192.168.20.250-192.168.20.254"
    )


def test_diff_ignores_ipv6_textual_drift() -> None:
    """Authored ``::ffff:0:0/96`` vs live ``::ffff:0.0.0.0/96`` -> no op."""
    from rsc.diff import Config, Item, diff

    live = Config()
    live.add(Item(menu="/ipv6/firewall/address-list", verb="add", props={
        "list": "iac.al6.v4mapped",
        "address": "::ffff:0.0.0.0/96",
    }))
    candidate = Config()
    candidate.add(Item(menu="/ipv6/firewall/address-list", verb="add", props={
        "list": "iac.al6.v4mapped",
        "address": "::ffff:0:0/96",
    }))

    assert diff(live, candidate) == []
    assert diff(candidate, live) == []


# --- verifier: built-in rows omitted by /export -----------------------------


def test_apply_patch_synthesises_builtin_row_on_set() -> None:
    """``set [find KEY=VAL] …`` against an empty menu must materialise the row.

    RouterOS /export omits built-in rows that match defaults (e.g. the
    ``admin`` user, default-named ``etherN`` interfaces). On the live
    router those rows exist from boot, so the rollforward
    ``set [find name=admin] password=…`` correctly updates them. The
    verifier must mirror that or it reports phantom drift on every
    deploy that touches such a row.
    """
    import tempfile
    from pathlib import Path
    from rsc.diff import Config, Item
    from rsc.diff.verify import apply_patch, residual_ops

    # Live: /user is empty (admin row omitted by /export).
    live = Config()
    # Candidate: explicit set on the boot-default admin row.
    candidate = Config()
    candidate.add(Item(menu="/user", verb="set", props={
        "__selector__": "[find name=admin]",
        "name": "admin",
        "password": "s3cret",
        "comment": '"Default admin"',
    }))

    # Hand-rolled patch text -- the same shape rsc.diff.emit produces.
    patch_text = (
        "/user\n"
        '    set [find name=admin] password=s3cret comment="Default admin"\n'
    )
    with tempfile.TemporaryDirectory() as td:
        patch_path = Path(td) / "up.rsc"
        patch_path.write_text(patch_text, encoding="utf-8")
        applied = apply_patch(live, patch_path)

        # The synthesised row should now satisfy the candidate target:
        # no residual ops once the verifier replays the diff.
        drift = residual_ops(applied, candidate)
        assert drift == [], drift


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
    test_normalise_value_canonicalises_ipv6()
    test_normalise_value_preserves_host_bits_in_prefix()
    test_normalise_value_passes_non_addresses_through()
    test_normalise_value_handles_lists_and_ranges()
    test_diff_ignores_ipv6_textual_drift()
    print("ok")
