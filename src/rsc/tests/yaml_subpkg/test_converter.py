"""Tests for rsc.yaml -- YAML profile sources rendered to .rsc text."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsc.yaml import YamlError, to_rsc, to_rsc_file  # noqa: E402


# --- module shape -----------------------------------------------------------


def test_renders_simple_add_with_id_and_comment() -> None:
    out = to_rsc(textwrap.dedent("""\
        interface:
          list:
            - operation: add
              id: iac.list.wan
              comment: WAN uplink
              name: iac.list.wan
    """))
    assert "/interface/list" in out
    assert 'add comment="iac.list.wan -- WAN uplink" name=iac.list.wan' in out


def test_set_filter_with_equals_wraps_in_find() -> None:
    """`filter: name=admin` becomes `set [find name=admin]`."""
    out = to_rsc(textwrap.dedent("""\
        user:
          - operation: set
            filter: name=admin
            comment: Default admin
            password: secret
    """))
    assert "/user" in out
    assert (
        'set [find name=admin] comment="Default admin" password=secret' in out
    )


def test_set_filter_bare_token_stays_bare() -> None:
    """`/ip/service` rows use bare names (`set telnet ...`), not [find ...]."""
    out = to_rsc(textwrap.dedent("""\
        ip:
          service:
            - operation: set
              filter: telnet
              disabled: 'yes'
            - operation: set
              filter: ssh
              address: '192.168.10.0/24'
    """))
    lines = out.strip().splitlines()
    assert "/ip/service" in lines
    assert any(line.strip() == "set telnet disabled=yes" for line in lines), out
    assert any(
        line.strip() == "set ssh address=192.168.10.0/24" for line in lines
    ), out
    # No `[find` introduced for bare names.
    assert "[find telnet]" not in out
    assert "[find ssh]" not in out


def test_var_reference_renders_as_dollar_name() -> None:
    out = to_rsc(textwrap.dedent("""\
        interface:
          wifi:
            security:
              - operation: add
                id: iac.wifi.sec.lan
                name: iac.wifi.sec.lan
                passphrase:
                  var: wifiIntPass
    """))
    assert "passphrase=$wifiIntPass" in out
    # No braces / quotes leaked into the rendered form.
    assert "{" not in out
    assert '"$' not in out


def test_expr_reference_renders_with_brackets() -> None:
    out = to_rsc(textwrap.dedent("""\
        interface:
          bridge:
            _items:
              - operation: add
                id: iac.bridge.lan
                name: iac.bridge.lan
                admin-mac:
                  expr: '/interface/ethernet get [find name=iac.ether.lan1] mac-address'
    """))
    assert (
        "admin-mac=[/interface/ethernet get [find name=iac.ether.lan1] mac-address]"
        in out
    )
    # Bracket expression must NOT be quoted -- that would change semantics.
    assert 'admin-mac="[' not in out


def test_items_sentinel_renders_at_current_path() -> None:
    """`_items` is rendered for the menu at its current path; sibling keys
    become child sub-menus."""
    out = to_rsc(textwrap.dedent("""\
        interface:
          bridge:
            _items:
              - operation: add
                id: iac.bridge.lan
                name: iac.bridge.lan
            port:
              - operation: add
                id: iac.bport.lan1
                bridge: iac.bridge.lan
                interface: iac.ether.lan1
    """))
    # Both menus appear.
    assert "/interface/bridge" in out
    assert "/interface/bridge/port" in out
    # `_items` came first in the YAML -> /interface/bridge appears first.
    assert out.index("/interface/bridge\n") < out.index("/interface/bridge/port")


def test_walk_preserves_yaml_insertion_order() -> None:
    """Sub-menu order in the rendered .rsc matches YAML mapping order."""
    out = to_rsc(textwrap.dedent("""\
        interface:
          wifi:
            datapath:
              - operation: add
                id: a
                name: a
            security:
              - operation: add
                id: b
                name: b
            _items:
              - operation: set
                filter: default-name=wifi1
                name: c
    """))
    dp = out.index("/interface/wifi/datapath")
    sec = out.index("/interface/wifi/security")
    items = out.index("/interface/wifi\n")
    assert dp < sec < items, out


def test_quoting_picks_up_spaces_and_specials() -> None:
    out = to_rsc(textwrap.dedent("""\
        system:
          identity:
            - operation: set
              name: 'Some Router'
    """))
    assert 'set name="Some Router"' in out


def test_yes_no_strings_pass_through_bare() -> None:
    out = to_rsc(textwrap.dedent("""\
        ip:
          service:
            - operation: set
              filter: telnet
              disabled: 'yes'
    """))
    # 'yes' has no special chars -> stays bare.
    assert "disabled=yes" in out
    # Defensive: never wrap yes/no in quotes.
    assert 'disabled="yes"' not in out


def test_empty_string_keeps_explicit_quotes() -> None:
    out = to_rsc(textwrap.dedent("""\
        system:
          routerboard:
            wps-button:
              - operation: set
                on-event: ''
    """))
    assert 'on-event=""' in out


def test_id_only_rendered_as_iac_token_alone() -> None:
    """When only `id` is present, the comment carries just the id."""
    out = to_rsc(textwrap.dedent("""\
        interface:
          list:
            - operation: add
              id: iac.list.wan
              name: iac.list.wan
    """))
    assert 'comment="iac.list.wan"' in out


def test_id_pad_widens_separator_for_visual_alignment() -> None:
    """`id_pad: N` puts N spaces between the id and the `--` separator.

    Used to preserve column-aligned `--` markers in the original .rsc
    sources (so bundles stay byte-equivalent with what was authored /
    deployed and `rsc diff` doesn't report cosmetic-only drift).
    """
    out = to_rsc(textwrap.dedent("""\
        ipv6:
          firewall:
            address-list:
              - operation: add
                id: iac.al6.unspec
                id_pad: 4
                comment: Unspecified address
                address: ::/128
                list: bad_ipv6
              - operation: add
                id: iac.al6.sitelocal
                comment: Site-local
                address: fec0::/10
                list: bad_ipv6
    """))
    # Padded entry: 4 spaces between id and `--`.
    assert 'comment="iac.al6.unspec    -- Unspecified address"' in out
    # Default entry: single space (id_pad omitted -> default 1).
    assert 'comment="iac.al6.sitelocal -- Site-local"' in out


def test_id_pad_zero_or_negative_rejected() -> None:
    with pytest.raises(YamlError, match="id_pad must be an integer >= 1"):
        to_rsc(textwrap.dedent("""\
            interface:
              list:
                - operation: add
                  id: iac.list.wan
                  id_pad: 0
                  comment: WAN uplink
                  name: iac.list.wan
        """))


def test_comment_only_rendered_alone() -> None:
    """When only `comment` is present, the rendered comment is just the text."""
    out = to_rsc(textwrap.dedent("""\
        user:
          - operation: set
            filter: name=admin
            comment: Default admin
    """))
    assert 'comment="Default admin"' in out


def test_no_comment_no_id_omits_comment_property() -> None:
    out = to_rsc(textwrap.dedent("""\
        system:
          clock:
            - operation: set
              time-zone-name: Europe/Prague
    """))
    assert "comment=" not in out


def test_description_rendered_as_banner() -> None:
    out = to_rsc(textwrap.dedent("""\
        description: |
          Module-level narrative.
          Second line.

        system:
          clock:
            - operation: set
              time-zone-name: UTC
    """))
    assert out.splitlines()[0] == "# Module-level narrative."
    assert "# Second line." in out
    assert "/system/clock" in out


def test_top_level_map_to_list_works() -> None:
    """A top-level key may map directly to a list (e.g. /user)."""
    out = to_rsc(textwrap.dedent("""\
        user:
          - operation: set
            filter: name=admin
            password: x
    """))
    assert "/user" in out
    assert "set [find name=admin] password=x" in out


def test_empty_input_renders_empty_string() -> None:
    assert to_rsc("") == ""
    assert to_rsc("   \n  \n") == ""


# --- globals shape ----------------------------------------------------------


def test_globals_render_as_global_declarations() -> None:
    out = to_rsc(textwrap.dedent("""\
        globals:
          - name: adminPass
            value: secret-pw
          - name: routerName
            value: MikroTik
    """))
    assert ':global adminPass "secret-pw"' in out
    assert ':global routerName "MikroTik"' in out


def test_globals_per_entry_description_becomes_comment_block() -> None:
    out = to_rsc(textwrap.dedent("""\
        globals:
          - name: adminCidrs
            value: 192.168.10.2/32
            description: |
              Sources permitted to reach winbox/ssh/www-ssl.
              All entries must be on the int subnet.
    """))
    assert "# Sources permitted to reach winbox/ssh/www-ssl." in out
    assert "# All entries must be on the int subnet." in out
    assert ':global adminCidrs "192.168.10.2/32"' in out
    # Comment lines come BEFORE the :global they describe.
    assert (
        out.index("# Sources permitted")
        < out.index(":global adminCidrs")
    )


def test_globals_empty_value_renders_as_quoted_empty() -> None:
    out = to_rsc(textwrap.dedent("""\
        globals:
          - name: adminPass
            value: ''
    """))
    assert ':global adminPass ""' in out


# --- error paths ------------------------------------------------------------


def test_malformed_yaml_raises_yamlerror() -> None:
    with pytest.raises(YamlError):
        to_rsc("interface:\n  list:\n  - bad: indent\n   nope")


def test_missing_operation_raises() -> None:
    with pytest.raises(YamlError, match="missing `operation`"):
        to_rsc(textwrap.dedent("""\
            interface:
              list:
                - name: iac.list.wan
        """))


def test_unknown_value_mapping_raises() -> None:
    with pytest.raises(YamlError, match="must have `var` or `expr`"):
        to_rsc(textwrap.dedent("""\
            interface:
              list:
                - operation: add
                  name:
                    bogus: x
        """))


def test_top_level_must_be_mapping() -> None:
    with pytest.raises(YamlError, match="must be a mapping"):
        to_rsc("- not: a mapping\n")


def test_to_rsc_file_reports_path_in_error(tmp_path: Path) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text("interface:\n  list:\n    - {}\n", encoding="utf-8")
    with pytest.raises(YamlError) as exc:
        to_rsc_file(bad)
    assert "broken.yaml" in str(exc.value)


def test_to_rsc_file_reads_and_renders(tmp_path: Path) -> None:
    src = tmp_path / "10-foo.yaml"
    src.write_text(
        "system:\n  clock:\n    - operation: set\n      time-zone-name: UTC\n",
        encoding="utf-8",
    )
    out = to_rsc_file(src)
    assert "/system/clock" in out
    assert "set time-zone-name=UTC" in out
