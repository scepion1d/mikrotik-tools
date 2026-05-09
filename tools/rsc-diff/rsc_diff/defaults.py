"""Per-menu property defaults and computed-property tables.

SAFETY MODEL
------------
This module is the most security-sensitive part of rsc-diff. A wrong entry
silently turns a real drift into a no-op -- meaning a deploy could leave
the router in an unintended state without anyone noticing.

Rules for adding entries:

1. **Verify against an actual ``/export`` from a router.** RouterOS export
   omits a property if and only if the property's current value equals its
   default. So if you see ``proto-mode`` in the export, you know it's NOT
   at the default. If you don't see it, you know it IS at the default.

2. **Same default across all sub-menus / item types?** Some properties
   (e.g. ``disabled``) default differently per menu. Encode the default
   under the most specific menu path you observed it for. Don't assume
   it transfers.

3. **Conservative bias.** If you're unsure, leave it out. The cost of a
   missing entry is some phantom drift in the diff (annoying but visible).
   The cost of a wrong entry is silent erasure of a real change (dangerous).

4. **Run with ``--strict``** to bypass this entire module if you ever
   need bit-exact comparison. Always do this for the FIRST diff against
   any new router so you can see the raw delta.

COMPUTED PROPERTIES
-------------------
Distinct from defaults: properties the router auto-derives from other
inputs and includes in export but the user never sets directly. Example:
``/ip/address network=192.168.10.0`` is computed from ``address=192.168.10.1/24``.
These are stripped before comparison and never emitted in patches.
"""

from __future__ import annotations


# Per-menu default values. A property absent from the parsed item is
# treated as if it carried this value; conversely, an explicit value
# matching the default is normalised to "absent" before comparison.
#
# Format: { menu_path: { property_name: default_value_string } }
#
# Sources of truth for each entry are noted in the comment beside it.
MENU_DEFAULTS: dict[str, dict[str, str]] = {
    # /interface/bridge protocol-mode: confirmed by checking that an
    # authored `protocol-mode=rstp` was OMITTED from the router's
    # /export output -- which only happens when the value equals the
    # default. See out/stable-7.54.backup.rsc /interface bridge section.
    "/interface/bridge": {
        "protocol-mode": "rstp",
    },
    # /ip/dhcp-server/lease lease-time: same evidence path.
    # `0s` is RouterOS's documented "static / no expiry" default for
    # leases added by config (vs leases assigned dynamically).
    "/ip/dhcp-server/lease": {
        "lease-time": "0s",
    },
    # /disk/settings auto-media-sharing / auto-smb-sharing: confirmed
    # absent from out/live.rsc which has no `/disk/settings set
    # auto-media-sharing=...` despite the candidate setting both to `no`.
    # RouterOS only includes /disk settings in /export when at least one
    # property differs from default; live emits only auto-media-interface,
    # so the two sharing flags are at their defaults.
    "/disk/settings": {
        "auto-media-sharing": "no",
        "auto-smb-sharing": "no",
    },
    # /ip/dhcp-server disabled=no: confirmed by live.rsc emitting
    # `add address-pool=... interface=... name=iac.dhcp.lan` with no
    # `disabled=` attribute, while the candidate sets `disabled=no`
    # explicitly. /export omits the prop when it matches the default.
    "/ip/dhcp-server": {
        "disabled": "no",
    },
}


# Properties RouterOS auto-derives from other inputs. Always stripped
# from the parsed model on both sides; never emitted in ops.
#
# Format: { menu_path: frozenset(property_name, ...) }
#
# Same evidence model as MENU_DEFAULTS: include only props you've seen
# the export emit AND know to be auto-derived (typically because the
# user never sets them via /menu add or /menu set).
MENU_COMPUTED: dict[str, frozenset[str]] = {
    # /ip/address `network` is computed from `address=A/B`.
    # Including it in a diff would emit a `set network=...` op, which
    # RouterOS would either reject or silently override at apply time.
    "/ip/address": frozenset({"network"}),
}


def is_default(menu: str, prop: str, value: str) -> bool:
    """True iff *value* matches the documented default for *menu*.*prop*."""
    return MENU_DEFAULTS.get(menu, {}).get(prop) == value


def is_computed(menu: str, prop: str) -> bool:
    """True iff *prop* on *menu* is a router-computed property."""
    return prop in MENU_COMPUTED.get(menu, frozenset())
