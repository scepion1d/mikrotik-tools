"""Smoke tests for parse_file / parse_text / entity_id."""

from __future__ import annotations

import sys
from pathlib import Path

# Make `rsc_parser` importable when running this file directly with `python`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsc_parser import (  # noqa: E402
    Config,
    Item,
    entity_id,
    is_synthetic,
    parse_file,
    parse_text,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def test_parse_text_empty() -> None:
    cfg = parse_text("")
    assert cfg.menus() == []


def test_parse_minimal_smoke() -> None:
    cfg = parse_file(FIX / "minimal.rsc")
    menus = set(cfg.menus())
    assert "/interface/list" in menus
    assert "/ip/dns" in menus
    assert "/ip/firewall/filter" in menus

    list_items = cfg.items_by_menu["/interface/list"]
    assert len(list_items) == 2
    assert list_items[0].props.get("name") == "iac.list.wan"


def test_logical_lines_fold_continuations() -> None:
    text = (
        "/interface/bridge\n"
        '    add name=\\\n'
        "        iac.bridge.lan \\\n"
        "        protocol-mode=rstp\n"
    )
    cfg = parse_text(text)
    item = cfg.items_by_menu["/interface/bridge"][0]
    assert item.props["name"] == "iac.bridge.lan"
    assert item.props["protocol-mode"] == "rstp"


def test_quoted_value_preserved_with_quotes() -> None:
    cfg = parse_text(
        "/interface/list\n"
        '    add comment="iac.list.wan -- WAN uplink" name=iac.list.wan\n'
    )
    item = cfg.items_by_menu["/interface/list"][0]
    # Parser keeps the surrounding quotes so emitter can echo back verbatim.
    assert item.props["comment"] == '"iac.list.wan -- WAN uplink"'


def test_set_with_find_selector_records_selector() -> None:
    cfg = parse_text(
        "/interface/ethernet\n"
        "    set [find default-name=ether1] name=iac.ether.wan\n"
    )
    item = cfg.items_by_menu["/interface/ethernet"][0]
    assert item.verb == "set"
    assert item.props["__selector__"] == "[find default-name=ether1]"
    assert item.props["name"] == "iac.ether.wan"


def test_set_with_positional_selector() -> None:
    cfg = parse_text(
        "/ip/service\n"
        "    set telnet disabled=yes\n"
    )
    item = cfg.items_by_menu["/ip/service"][0]
    assert item.verb == "set"
    assert item.props["__selector__"] == "telnet"
    assert item.props["disabled"] == "yes"


# --- entity_id ----------------------------------------------------------------


def test_entity_id_from_name() -> None:
    item = Item(
        menu="/interface/bridge", verb="add",
        props={"name": "iac.bridge.lan"},
    )
    assert entity_id(item, 0) == "iac.bridge.lan"
    assert is_synthetic(item) is False


def test_entity_id_from_comment_token() -> None:
    item = Item(
        menu="/ip/firewall/filter", verb="add",
        props={"comment": '"iac.fw.filter.input.1 -- Accept est/related"'},
    )
    assert entity_id(item, 0) == "iac.fw.filter.input.1"
    assert is_synthetic(item) is False


def test_entity_id_synthetic_for_singleton() -> None:
    item = Item(menu="/system/clock", verb="set",
                props={"time-zone-name": "Europe/Prague"})
    assert entity_id(item, 0) == "iac.system.clock"
    assert is_synthetic(item) is True


def test_entity_id_synthetic_for_ip_service() -> None:
    # `set telnet disabled=yes` -- positional selector becomes the tag.
    item = Item(menu="/ip/service", verb="set",
                props={"__selector__": "telnet", "disabled": "yes"})
    assert entity_id(item, 0) == "iac.ip.service.telnet"
    assert is_synthetic(item) is True


def test_entity_id_synthetic_for_admin_user() -> None:
    item = Item(menu="/user", verb="set",
                props={"__selector__": "[find name=admin]",
                       "comment": "Default admin"})
    assert entity_id(item, 0) == "iac.user.admin"
    assert is_synthetic(item) is True


def test_entity_id_synthetic_for_default_named_ether() -> None:
    # Hardware not yet renamed: synthetic id from default-name.
    item = Item(menu="/interface/ethernet", verb="set",
                props={"__selector__": "[find default-name=ether7]"})
    assert entity_id(item, 0) == "iac.interface.ethernet.ether7"
    assert is_synthetic(item) is True


def test_entity_id_positional_for_ordered_menu_no_id() -> None:
    item = Item(menu="/ip/firewall/filter", verb="add",
                props={"chain": "input", "action": "accept"})
    assert entity_id(item, 3) == "iac.ip.firewall.filter.3"
    assert is_synthetic(item) is True


def test_entity_id_anon_fallback() -> None:
    item = Item(menu="/interface/bridge/port", verb="add",
                props={"interface": "iac.ether.lan1"})
    assert entity_id(item, 2) == "iac.interface.bridge.port.@2"
    assert is_synthetic(item) is True


# --- back-compat: identity_key still works exactly as before ------------------


def test_identity_key_unchanged_for_named_menu() -> None:
    item = Item(menu="/interface/bridge", verb="add",
                props={"name": "iac.bridge.lan"})
    assert item.identity_key(0) == "name=iac.bridge.lan"


def test_identity_key_unchanged_for_singleton() -> None:
    item = Item(menu="/system/clock", verb="set",
                props={"time-zone-name": "Europe/Prague"})
    assert item.identity_key(0) == "/system/clock"


def test_identity_key_unchanged_for_comment_token() -> None:
    item = Item(menu="/ip/firewall/filter", verb="add",
                props={"comment": '"iac.fw.filter.input.1 -- Accept"'})
    assert item.identity_key(0) == "comment~iac.fw.filter.input.1"


def test_identity_key_unchanged_for_ordered_no_comment() -> None:
    item = Item(menu="/ip/firewall/filter", verb="add",
                props={"chain": "input"})
    assert item.identity_key(5) == "@pos=5"


def test_config_index_uses_identity_key() -> None:
    cfg = Config()
    cfg.add(Item(menu="/interface/list", verb="add",
                 props={"name": "iac.list.wan"}))
    cfg.add(Item(menu="/interface/list", verb="add",
                 props={"name": "iac.list.lan"}))
    idx = cfg.index("/interface/list")
    assert set(idx.keys()) == {"name=iac.list.wan", "name=iac.list.lan"}
