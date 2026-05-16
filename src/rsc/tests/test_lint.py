"""Tests for rsc.lint -- semantic checks on parsed configs."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsc.lint import (  # noqa: E402
    LintIssue,
    Severity,
    format_issues,
    lint,
)
from rsc.parser import parse_text  # noqa: E402


def _lint(rsc_text: str) -> list[LintIssue]:
    return lint(parse_text(textwrap.dedent(rsc_text)))


# --- LINT001 duplicate-id --------------------------------------------------


def test_duplicate_iac_id_in_same_menu_is_error() -> None:
    issues = _lint("""\
        /interface/list
            add comment="iac.list.wan -- A" name=iac.list.wan
            add comment="iac.list.wan -- B" name=iac.list.wan
    """)
    codes = [i.code for i in issues]
    assert codes == ["LINT001", "LINT001"]  # one per occurrence
    assert all(i.severity is Severity.ERROR for i in issues)
    assert all(i.id == "iac.list.wan" for i in issues)


def test_same_iac_id_in_different_menus_is_fine() -> None:
    """The same iac.* token is OK across menus -- it identifies the
    item *within* its menu, not globally."""
    issues = _lint("""\
        /interface/list
            add comment="iac.x -- list" name=iac.x
        /interface/bridge
            add comment="iac.x -- bridge" name=iac.x
    """)
    assert [i.code for i in issues] == []


def test_synthetic_ids_are_not_flagged_as_duplicates() -> None:
    """Items without an iac.* token get synthetic positional ids; those
    are unique by construction and must NOT trigger LINT001."""
    # Two firewall rules with no iac id -- parser assigns positional ids.
    issues = _lint("""\
        /ip/firewall/filter
            add chain=input action=accept
            add chain=input action=drop
    """)
    assert [i for i in issues if i.code == "LINT001"] == []


# --- LINT002 dangling-reference -------------------------------------------


def test_interface_ref_to_existing_bridge_is_clean() -> None:
    issues = _lint("""\
        /interface/bridge
            add comment="iac.bridge.lan -- LAN" name=iac.bridge.lan

        /ip/address
            add comment="iac.addr.lan -- router" address=192.168.1.1/24 interface=iac.bridge.lan
    """)
    assert [i.code for i in issues] == []


def test_interface_ref_to_missing_entity_is_error() -> None:
    issues = _lint("""\
        /ip/address
            add comment="iac.addr.lan -- router" address=192.168.1.1/24 interface=iac.bridge.missing
    """)
    codes = [i.code for i in issues]
    assert "LINT002" in codes
    msg = next(i for i in issues if i.code == "LINT002").message
    assert "interface" in msg and "iac.bridge.missing" in msg


def test_non_iac_interface_ref_is_skipped() -> None:
    """Bare names like `ether1` aren't iac.* -- we can't verify them
    without router-side state, so don't flag."""
    issues = _lint("""\
        /ip/address
            add comment="iac.addr.lan -- router" address=192.168.1.1/24 interface=ether1
    """)
    assert [i.code for i in issues] == []


def test_address_list_self_definition_is_clean() -> None:
    """`list=iac.al.quarantine` inside /ip/firewall/address-list CREATES
    the grouping label; doesn't need to exist anywhere else first.
    Same for /ipv6/firewall/address-list."""
    issues = _lint("""\
        /ip/firewall/address-list
            add comment="iac.al.q.int" list=iac.al.quarantine address=192.168.10.250
            add comment="iac.al.q.iot" list=iac.al.quarantine address=192.168.20.250
    """)
    assert [i.code for i in issues] == []


def test_src_address_list_ref_to_existing_group_is_clean() -> None:
    issues = _lint("""\
        /ip/firewall/address-list
            add comment="iac.al.q.int" list=iac.al.quarantine address=192.168.10.250

        /ip/firewall/filter
            add comment="iac.fw.f.q -- drop quarantine" chain=input action=drop src-address-list=iac.al.quarantine
    """)
    assert [i.code for i in issues] == []


def test_src_address_list_ref_to_missing_group_is_error() -> None:
    issues = _lint("""\
        /ip/firewall/filter
            add comment="iac.fw.f.q" chain=input action=drop src-address-list=iac.al.nonexistent
    """)
    msgs = [i.message for i in issues if i.code == "LINT002"]
    assert any("iac.al.nonexistent" in m for m in msgs)


def test_interface_list_name_lookup_works() -> None:
    """`/interface/list/member list=iac.list.wan` looks for an
    /interface/list entry with name=iac.list.wan."""
    # Clean case: list exists.
    issues = _lint("""\
        /interface/list
            add comment="iac.list.wan -- WAN" name=iac.list.wan

        /interface/list/member
            add comment="iac.m.wan" list=iac.list.wan interface=iac.ether.wan
    """)
    # The interface= ref will still fail (no iac.ether.wan defined here),
    # but list= should NOT.
    list_issues = [i for i in issues if i.code == "LINT002" and "list=" in i.message]
    assert list_issues == [], list_issues

    # Dirty case: list doesn't exist.
    issues = _lint("""\
        /interface/list/member
            add comment="iac.m.wan" list=iac.list.nope interface=ether1
    """)
    assert any(i.code == "LINT002" and "iac.list.nope" in i.message for i in issues)


def test_comma_separated_list_values_checked_per_token() -> None:
    """`tagged=iac.bridge.a,iac.bridge.b` -- check each token."""
    # Defining `tagged=` is rare; use interface= which is more typical
    # for the list-form. /interface/bridge/vlan uses tagged= but the
    # current ref table doesn't include it -- that's intentional (we
    # don't yet check tagged=). Skip this concern with a direct
    # comma-split test on an in-interface-list= prop.
    issues = _lint("""\
        /interface/list
            add comment="iac.list.a -- A" name=iac.list.a

        /ip/firewall/filter
            add comment="iac.fw.f.x" chain=input action=accept in-interface-list=iac.list.a,iac.list.missing
    """)
    # iac.list.a exists, iac.list.missing doesn't.
    bad = [i for i in issues if i.code == "LINT002" and "iac.list.missing" in i.message]
    assert len(bad) == 1


def test_leading_bang_negation_is_stripped() -> None:
    """`in-interface-list=!iac.list.lan` -- negation strip should not
    mask a real dangling ref."""
    issues = _lint("""\
        /ip/firewall/filter
            add comment="iac.fw.f.x" chain=input action=drop in-interface-list=!iac.list.nope
    """)
    assert any(
        i.code == "LINT002" and "iac.list.nope" in i.message
        for i in issues
    )


# --- LINT005 orphan-pool-ref ----------------------------------------------


def test_dhcp_server_pool_ref_to_existing_pool_is_clean() -> None:
    issues = _lint("""\
        /ip/pool
            add comment="iac.pool.lan -- LAN pool" name=iac.pool.lan ranges=10.0.0.2-10.0.0.250

        /ip/dhcp-server
            add comment="iac.dhcp.lan -- LAN" name=iac.dhcp.lan interface=iac.bridge.lan address-pool=iac.pool.lan
    """)
    # interface= ref will fail (no bridge defined), but pool ref should be clean.
    pool_issues = [i for i in issues if i.code == "LINT005"]
    assert pool_issues == []


def test_dhcp_server_pool_ref_to_missing_pool_is_silent_failure_error() -> None:
    issues = _lint("""\
        /ip/dhcp-server
            add comment="iac.dhcp.lan" name=iac.dhcp.lan address-pool=iac.pool.missing
    """)
    pool = [i for i in issues if i.code == "LINT005"]
    assert len(pool) == 1
    assert "iac.pool.missing" in pool[0].message
    assert "silent" in pool[0].message.lower() or "lease nothing" in pool[0].message.lower()


def test_dhcp_server_static_only_sentinel_accepted() -> None:
    """`address-pool=static-only` is a RouterOS sentinel meaning 'no
    dynamic leases'. Must NOT trigger LINT005."""
    issues = _lint("""\
        /ip/dhcp-server
            add comment="iac.dhcp.lan" name=iac.dhcp.lan address-pool=static-only
    """)
    assert [i for i in issues if i.code == "LINT005"] == []


# --- format_issues ---------------------------------------------------------


def test_format_issues_clean_message() -> None:
    out = format_issues([])
    assert "clean" in out


def test_format_issues_groups_errors_and_warnings() -> None:
    issues = [
        LintIssue(Severity.ERROR, "LINT001", "/x", 0, "iac.x", "dup"),
        LintIssue(Severity.WARNING, "LINT003", "/y", None, None, "unused"),
    ]
    out = format_issues(issues)
    assert "2 issue(s)" in out
    assert "1 error(s)" in out
    assert "1 warning(s)" in out
    assert "LINT001" in out and "LINT003" in out


def test_issues_sorted_by_code_then_menu() -> None:
    """Output order is stable: code asc, then menu asc."""
    issues = _lint("""\
        /interface/list
            add comment="iac.list.wan -- A" name=iac.list.wan
            add comment="iac.list.wan -- B" name=iac.list.wan

        /ip/firewall/filter
            add comment="iac.fw.x" chain=input action=accept in-interface-list=iac.list.missing
    """)
    codes = [i.code for i in issues]
    # Sort guarantees: LINT001 (two entries) before LINT002.
    assert codes[:2] == ["LINT001", "LINT001"]
    assert codes[2] == "LINT002"
