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

    by_kind = {"add": 0, "set": 0, "remove": 0}
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


if __name__ == "__main__":
    test_parse_empty()
    test_parse_minimal()
    test_diff_a_to_b_emits_expected_ops()
    test_diff_self_is_empty()
    print("ok")
