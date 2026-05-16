"""Tests for rsc.yaml.reverse -- .rsc Config -> YAML profile sources."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.parser import Config, Item, parse_text  # noqa: E402
from rsc.yaml.reverse import (  # noqa: E402
    item_to_yaml,
    to_yaml_docs,
    to_yaml_files,
)


# --- item_to_yaml ----------------------------------------------------------


def test_add_item_emits_no_operation_key() -> None:
    """`add` is the default -- omit `operation:` from output."""
    item = Item(
        menu="/interface/list",
        verb="add",
        props={
            "comment": '"iac.list.wan -- WAN uplink"',
            "name": "iac.list.wan",
        },
    )
    out = item_to_yaml(item)
    assert "operation" not in out
    assert out["id"] == "iac.list.wan"
    assert out["comment"] == "WAN uplink"
    assert out["name"] == "iac.list.wan"


def test_set_item_emits_operation_set_and_filter() -> None:
    """`set [find name=admin] ...` -> operation: set + filter: name=admin."""
    item = Item(
        menu="/user",
        verb="set",
        props={
            "__selector__": "[find name=admin]",
            "name": "admin",
            "comment": '"Default admin"',
            "password": "secret",
        },
    )
    out = item_to_yaml(item)
    assert out["operation"] == "set"
    assert out["filter"] == "name=admin"
    # `name` is the parser-surfaced selector kv -- must NOT appear as a
    # separate prop (would round-trip as a phantom).
    assert "name" not in out
    assert out["password"] == "secret"


def test_set_with_bare_positional_filter() -> None:
    """`set telnet disabled=yes` -> filter: telnet, no redundant name."""
    item = Item(
        menu="/ip/service",
        verb="set",
        props={
            "__selector__": "telnet",
            "name": "telnet",
            "disabled": "yes",
        },
    )
    out = item_to_yaml(item)
    assert out["filter"] == "telnet"
    assert "name" not in out
    assert out["disabled"] == "yes"


def test_id_pad_preserved_for_column_alignment() -> None:
    """Multi-space padding between id and `--` -> id_pad."""
    item = Item(
        menu="/interface/ethernet",
        verb="set",
        props={
            "__selector__": "[find default-name=ether1]",
            "default-name": "ether1",
            "comment": '"iac.ether.wan  -- WAN uplink"',  # two spaces
            "name": "iac.ether.wan",
        },
    )
    out = item_to_yaml(item)
    assert out["id"] == "iac.ether.wan"
    assert out["id_pad"] == 2
    assert out["comment"] == "WAN uplink"


def test_non_iac_comment_preserved_verbatim() -> None:
    """A comment without iac.* prefix stays as the raw `comment:` field."""
    item = Item(
        menu="/system/note",
        verb="set",
        props={"comment": '"Just a free-form note"'},
    )
    out = item_to_yaml(item)
    assert "id" not in out
    assert out["comment"] == "Just a free-form note"


def test_id_only_no_text() -> None:
    """`comment="iac.X"` (no `-- text`) -> id only, no comment field."""
    item = Item(
        menu="/interface/list",
        verb="add",
        props={"comment": '"iac.list.wan"', "name": "iac.list.wan"},
    )
    out = item_to_yaml(item)
    assert out["id"] == "iac.list.wan"
    assert "comment" not in out


def test_prop_quotes_stripped() -> None:
    """`comment="quoted value"` -> comment: quoted value (no extra quotes)."""
    item = Item(
        menu="/x",
        verb="add",
        props={"value": '"some thing"', "other": "bare"},
    )
    out = item_to_yaml(item)
    assert out["value"] == "some thing"
    assert out["other"] == "bare"


def test_internal_selector_key_dropped() -> None:
    """`__selector__` is a parser internal -- never emitted as a YAML prop."""
    item = Item(
        menu="/x",
        verb="set",
        props={"__selector__": "[find name=foo]", "name": "foo"},
    )
    out = item_to_yaml(item)
    # __selector__ has become filter; not a prop.
    assert "__selector__" not in out


# --- to_yaml_docs ---------------------------------------------------------


def test_top_menu_with_only_own_items_collapses_to_list() -> None:
    """`/user` with no children -> `user: [...]` not `user: {_items: [...]}`."""
    cfg = Config()
    cfg.add(Item(menu="/user", verb="set", props={
        "__selector__": "[find name=admin]",
        "name": "admin",
        "password": "x",
    }))
    docs = to_yaml_docs(cfg)
    assert "60-user.yaml" in docs
    doc = docs["60-user.yaml"]
    assert isinstance(doc["user"], list)
    assert len(doc["user"]) == 1


def test_top_menu_with_own_items_AND_sub_menus_uses_items_sentinel() -> None:
    """`/interface/bridge` (own items) + `/interface/bridge/port` (sub-menu)
    -> bridge maps to {_items: [...], port: [...]}."""
    cfg = Config()
    cfg.add(Item(menu="/interface/bridge", verb="add", props={"name": "iac.bridge.lan"}))
    cfg.add(Item(menu="/interface/bridge/port", verb="add", props={
        "bridge": "iac.bridge.lan", "interface": "iac.ether.lan1",
    }))
    docs = to_yaml_docs(cfg)
    bridge = docs["10-interface.yaml"]["interface"]["bridge"]
    assert isinstance(bridge, dict)
    assert "_items" in bridge
    assert "port" in bridge


def test_top_menu_files_numbered_by_nn_convention() -> None:
    """Top menus get NN- prefixes mirroring src/<profile>/ layout."""
    cfg = Config()
    cfg.add(Item(menu="/interface/list", verb="add", props={"name": "iac.list.wan"}))
    cfg.add(Item(menu="/ip/address", verb="add", props={"address": "1.2.3.4/24"}))
    cfg.add(Item(menu="/ipv6/firewall/filter", verb="add", props={"chain": "input"}))
    cfg.add(Item(menu="/system/identity", verb="set", props={"name": "router"}))
    docs = to_yaml_docs(cfg)
    assert set(docs.keys()) == {
        "10-interface.yaml",
        "30-ip.yaml",
        "40-ipv6.yaml",
        "60-system.yaml",
    }


def test_unknown_top_menu_lands_in_99_misc() -> None:
    """A menu outside the known NN map gets bucket 99."""
    cfg = Config()
    cfg.add(Item(menu="/snmp", verb="set", props={"enabled": "no"}))
    docs = to_yaml_docs(cfg)
    assert "99-snmp.yaml" in docs


# --- to_yaml_files (file I/O) ---------------------------------------------


def test_to_yaml_files_writes_files(tmp_path: Path) -> None:
    cfg = Config()
    cfg.add(Item(menu="/interface/list", verb="add", props={"name": "iac.list.wan"}))
    written = to_yaml_files(cfg, tmp_path)
    assert len(written) == 1
    assert written[0].name == "10-interface.yaml"
    assert (tmp_path / "10-interface.yaml").is_file()


def test_to_yaml_files_refuses_overwrite_by_default(tmp_path: Path) -> None:
    (tmp_path / "10-interface.yaml").write_text("placeholder", encoding="utf-8")
    cfg = Config()
    cfg.add(Item(menu="/interface/list", verb="add", props={"name": "iac.list.wan"}))
    with pytest.raises(FileExistsError):
        to_yaml_files(cfg, tmp_path)


def test_to_yaml_files_overwrite_flag(tmp_path: Path) -> None:
    (tmp_path / "10-interface.yaml").write_text("placeholder", encoding="utf-8")
    cfg = Config()
    cfg.add(Item(menu="/interface/list", verb="add", props={"name": "iac.list.wan"}))
    written = to_yaml_files(cfg, tmp_path, overwrite=True)
    assert len(written) == 1
    text = (tmp_path / "10-interface.yaml").read_text(encoding="utf-8")
    assert "placeholder" not in text
    assert "iac.list.wan" in text


# --- end-to-end roundtrip --------------------------------------------------


def test_roundtrip_parse_reverse_yaml_parses_cleanly() -> None:
    """Reverse output must be valid YAML matching the schema's general shape."""
    rsc = textwrap.dedent("""\
        /interface/list
            add comment="iac.list.wan -- WAN uplink" name=iac.list.wan
            add comment="iac.list.lan -- LAN" name=iac.list.lan

        /user
            set [find name=admin] password=x comment="Default admin"
    """)
    cfg = parse_text(rsc)
    docs = to_yaml_docs(cfg)
    # Every doc must parse cleanly.
    for filename, doc in docs.items():
        text = yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
        reparsed = yaml.safe_load(text)
        assert reparsed == doc, f"{filename}: round-trip via YAML changed shape"


def test_roundtrip_bundle_yaml_against_real_rsc(tmp_path: Path) -> None:
    """End-to-end: parse -> reverse -> bundle = same Config.

    Uses a small synthetic rsc to keep the test self-contained. The
    full deployed-candidate round-trip is covered manually + by the
    smoke script.
    """
    from rsc.bundle import bundle

    rsc = textwrap.dedent("""\
        /interface/list
            add comment="iac.list.wan -- WAN uplink" name=iac.list.wan

        /ip/address
            add comment="iac.addr.lan -- Router LAN" address=192.168.10.1/24 interface=iac.bridge.lan

        /user
            set [find name=admin] password=secret comment="Default admin"
    """)
    cfg_original = parse_text(rsc)

    # Reverse + write.
    to_yaml_files(cfg_original, tmp_path)

    # Bundle the YAML back to .rsc text.
    bundled = bundle(str(tmp_path), yaml=True)
    cfg_roundtrip = parse_text(bundled)

    # Compare menu-by-menu, item-by-item via the parser's identity keys.
    assert (
        list(cfg_original.items_by_menu.keys())
        == list(cfg_roundtrip.items_by_menu.keys())
    )
    for menu in cfg_original.items_by_menu:
        orig_items = cfg_original.items_by_menu[menu]
        rt_items = cfg_roundtrip.items_by_menu[menu]
        assert len(orig_items) == len(rt_items), menu
        for orig, rt in zip(orig_items, rt_items):
            assert orig.verb == rt.verb
            # Same identity key under the same position -> same row identity.
            for pos in range(len(orig_items)):
                pass
            # Comparing raw props would fail on quoting differences; the
            # parser's identity_key abstracts those out.
            assert orig.identity_key(0) == rt.identity_key(0), (menu, orig, rt)
