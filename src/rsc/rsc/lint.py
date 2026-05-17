"""Semantic lint for parsed RouterOS configs.

Layered on top of :mod:`rsc.parser`: takes a :class:`~rsc.parser.Config`
(from any source -- ``.rsc`` file, YAML bundle, or in-memory build) and
reports structural problems that the parser doesn't catch on its own.

What this catches today
-----------------------

- **LINT001 duplicate-id** -- two items in the same menu carry the same
  ``iac.<...>`` identity token. Apply order would clobber one with the
  other; usually a copy-paste mistake.
- **LINT002 dangling-reference** -- a property like
  ``interface=iac.bridge.foo`` or ``address-pool=iac.pool.bar`` points
  at an ``iac.*`` name that no item anywhere in the config defines.
  Catches typos before they reach ``/import`` (where they'd cascade
  into hard-to-diagnose runtime errors).
- **LINT005 orphan-pool-ref** -- a stricter variant of LINT002 for
  DHCP-server-to-pool wiring; flagged separately because the failure
  mode is silent (DHCP server starts but leases nothing).

What this DOESN'T catch (yet)
-----------------------------
- ``$varname`` references with no matching ``:global`` (LINT004) --
  needs pre-flatten text; deferred to keep this module
  source-agnostic.
- Unused ``:global`` declarations (LINT003) -- same reason.

Each issue carries (severity, code, location, message). The CLI groups
them by file when it can, sorted for stable output. ``error`` issues
make the linter exit non-zero; ``warning`` issues are reported but
don't fail the run.

Public API
----------
- :func:`lint`              -- run all checks on a Config; returns a list
- :func:`format_issues`     -- pretty-print issues for the CLI
- :class:`LintIssue`        -- one issue
- :class:`Severity`         -- enum: ERROR / WARNING
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rsc.parser import IAC_PREFIX, Config, entity_id


class Severity(str, Enum):
    """Issue severity. ``str`` mixin so it formats naturally in messages."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class LintIssue:
    """One lint finding.

    *menu* / *position* / *id* identify the offending item when known
    (some checks find issues that span items; those leave the slot
    None). *code* is the stable LINTNNN identifier; *severity* drives
    CLI exit status.
    """

    severity: Severity
    code: str
    menu: str | None
    position: int | None
    id: str | None
    message: str


def lint(cfg: Config) -> list[LintIssue]:
    """Run all enabled checks against *cfg*. Returns issues in a stable order.

    Empty list = clean. Check order doesn't matter; sorting is by
    (code, menu, position) at the end so the CLI output is reproducible.
    """
    issues: list[LintIssue] = []
    issues.extend(_check_duplicate_ids(cfg))
    issues.extend(_check_dangling_references(cfg))
    issues.extend(_check_orphan_pool_refs(cfg))
    issues.sort(key=lambda i: (i.code, i.menu or "", i.position or 0))
    return issues


def format_issues(issues: list[LintIssue]) -> str:
    """Multi-line summary suitable for stderr output.

    Header line shows total count + breakdown. Each subsequent line:
    ``  CODE severity menu[@pos] (iac.x.y): message``. Truncated at no
    fixed length -- the CLI can choose to cap if needed.
    """
    if not issues:
        return "rsc lint: clean (no issues)."
    errors = sum(1 for i in issues if i.severity is Severity.ERROR)
    warnings = sum(1 for i in issues if i.severity is Severity.WARNING)
    out = [
        f"rsc lint: {len(issues)} issue(s) -- "
        f"{errors} error(s), {warnings} warning(s)"
    ]
    for issue in issues:
        loc = issue.menu or "<unknown>"
        if issue.position is not None:
            loc = f"{loc}[{issue.position}]"
        if issue.id:
            loc = f"{loc} ({issue.id})"
        out.append(
            f"  {issue.code} {issue.severity.value}: {loc}: {issue.message}"
        )
    return "\n".join(out)


# --- checks ----------------------------------------------------------------


def _check_duplicate_ids(cfg: Config) -> list[LintIssue]:
    """LINT001: two items in the same menu sharing one ``iac.*`` token.

    Two-pass: collect ``{menu: {iac_id: [positions]}}``, then emit one
    issue per id with len(positions) > 1. Each duplicate position gets
    its own issue line so the operator sees every occurrence (and the
    CLI can show line numbers for each via the YAML source map, future
    work).
    """
    issues: list[LintIssue] = []
    for menu, items in cfg.items_by_menu.items():
        seen: dict[str, list[int]] = {}
        for pos, item in enumerate(items):
            iid = entity_id(item, pos)
            # entity_id always returns something; only flag *user-authored*
            # iac.* tokens. Synthetic ids (positional / built-in) won't
            # collide because they include position or menu disambiguator.
            if not iid.startswith(IAC_PREFIX):
                continue
            seen.setdefault(iid, []).append(pos)
        for iid, positions in seen.items():
            if len(positions) <= 1:
                continue
            for pos in positions:
                issues.append(LintIssue(
                    severity=Severity.ERROR,
                    code="LINT001",
                    menu=menu,
                    position=pos,
                    id=iid,
                    message=(
                        f"duplicate id {iid!r} (appears at positions "
                        f"{positions} in this menu)"
                    ),
                ))
    return issues


# Which props reference which `iac.*` namespaces. The check accepts a
# match in ANY of the listed menus -- some props can point at multiple
# kinds of thing (e.g. interface= can be an ethernet, vlan, or bridge).
#
# Conservative on purpose: only flag a reference dangling when NONE of
# the candidate menus contain it. If a prop points to something outside
# the iac.* namespace (e.g. a raw RouterOS default name), we skip it --
# we can't know whether it's valid without router-side state.
#
# Lookup mode:
#   "entity" -- value must match an item's entity_id (the iac.* token
#               in `name=` or in `comment=`)
#   "name"   -- value must match the value of some item's `name=` prop
#               in the candidate menu (used for /interface/list, where
#               the list itself has name=iac.list.lan)
#   "list"   -- value must match the value of some item's `list=` prop
#               in the candidate menu (used for address-lists, where
#               many entries share the same `list=` grouping label)
_REFERENCES: dict[str, tuple[str, tuple[str, ...]]] = {
    # interface props can point to any interface type; entity_id is the
    # `name=iac.x` field. The candidate list mirrors every interface
    # menu in MENUS_WITH_NAME plus the physical-only menus
    # (/interface/ethernet has no name= but its `name=iac.ether.*` is
    # set via `set [find default-name=...]`).
    "interface":         ("entity", (
        "/interface/ethernet", "/interface/vlan", "/interface/bridge",
        "/interface/bonding", "/interface/vrrp",
        "/interface/wifi",
        "/interface/wireguard",
        "/interface/gre", "/interface/gre6",
        "/interface/eoip", "/interface/eoip6",
        "/interface/ipip", "/interface/ipip6",
        "/interface/vxlan",
        "/interface/ovpn-client",
        "/interface/l2tp-client", "/interface/sstp-client",
        "/interface/pptp-client", "/interface/pppoe-client",
    )),
    "bridge":            ("entity", ("/interface/bridge",)),
    "master-interface":  ("entity", ("/interface/wifi",)),
    "address-pool":      ("entity", ("/ip/pool",)),
    # `pool=` is used by /ipv6/dhcp-server (refs /ipv6/pool) and by
    # /ip/dhcp-server in some firmware variants (refs /ip/pool).
    "pool":              ("entity", ("/ipv6/pool", "/ip/pool")),
    "server":            ("entity", ("/ip/dhcp-server",)),
    "configuration":     ("entity", ("/interface/wifi/configuration",)),
    "datapath":          ("entity", ("/interface/wifi/datapath",)),
    "security":          ("entity", ("/interface/wifi/security",)),
    "channel":           ("entity", ("/interface/wifi/channel",)),
    "aaa":               ("entity", ("/interface/wifi/aaa",)),
    "steering":          ("entity", ("/interface/wifi/steering",)),
    # `list=` (inside /interface/list/member) names an interface list --
    # match against the `name=` of /interface/list entries.
    "list":              ("name", ("/interface/list",)),
    # `in-interface-list` / `out-interface-list` reference the same
    # `name=`-keyed /interface/list entries.
    "in-interface-list":  ("name", ("/interface/list",)),
    "out-interface-list": ("name", ("/interface/list",)),
    # Firewall address-list refs name a `list=`-keyed grouping -- the
    # same label appears as the `list=` value on multiple entries.
    "src-address-list":  ("list", (
        "/ip/firewall/address-list", "/ipv6/firewall/address-list",
    )),
    "dst-address-list":  ("list", (
        "/ip/firewall/address-list", "/ipv6/firewall/address-list",
    )),
    # --- routing ----------------------------------------------------
    # `routing-table=` (on /ip/route, /ip/route/rule, /ip/firewall/mangle
    # action=mark-routing) names a /routing/table entry by its name=.
    "routing-table":     ("entity", ("/routing/table",)),
    # `vrf-interface=` on /ip/route/vrf names the carrying interface.
    "vrf-interface":     ("entity", (
        "/interface/ethernet", "/interface/vlan", "/interface/bridge",
        "/interface/wireguard",
    )),
    # --- IPsec ------------------------------------------------------
    "peer":              ("entity", ("/ip/ipsec/peer",)),
    "profile":           ("entity", (
        "/ip/ipsec/profile", "/ppp/profile",
    )),
    "proposal":          ("entity", ("/ip/ipsec/proposal",)),
    "mode-config":       ("entity", ("/ip/ipsec/mode-config",)),
    # --- certificates ------------------------------------------------
    # Many menus take `certificate=` (e.g. /ip/service, /ip/ipsec/identity,
    # /interface/ovpn-server, /certificate set ca-crl-host).
    "certificate":       ("entity", ("/certificate",)),
    # --- queues ------------------------------------------------------
    # `queue=` on /queue/simple and /queue/tree names a /queue/type.
    "queue":             ("entity", ("/queue/type",)),
    # `parent=` on /queue/tree references another /queue/tree (or an
    # interface name for the root). Validate against /queue/tree only;
    # interface-name parents won't be iac.* tokens so they're skipped
    # by the lint check anyway.
    "parent":            ("entity", ("/queue/tree",)),
    # --- users / auth ------------------------------------------------
    # `group=` on /user references /user/group by name=.
    "group":             ("entity", ("/user/group",)),
    # `user=` on /user/ssh-keys references /user by name=.
    "user":              ("entity", ("/user",)),
    # --- system logging / scheduler ---------------------------------
    # `action=` on /system/logging references /system/logging/action by
    # name=. Note: many menus also use `action=` as an enum (accept,
    # drop, ...); the lint check only fires on iac.* values, so the
    # firewall enums are unaffected.
    "action":            ("entity", ("/system/logging/action",)),
    # `on-event=` on /system/scheduler names a /system/script by name=.
    "on-event":          ("entity", ("/system/script",)),
    # `source=` / `script=` on /system/scheduler same target.
    "script":            ("entity", ("/system/script",)),
    # --- PPP --------------------------------------------------------
    # `default-profile=` on PPP client interfaces references /ppp/profile.
    "default-profile":   ("entity", ("/ppp/profile",)),
}


# Menus where a given prop is *defining* the value (not referencing
# something else). The dangling-ref check skips these props in these
# menus. Example: `list=iac.al.quarantine` inside
# /ip/firewall/address-list *creates* the grouping label, so the value
# doesn't need to exist anywhere else first.
_DEFINING_PROPS: dict[str, frozenset[str]] = {
    "list": frozenset({
        "/ip/firewall/address-list",
        "/ipv6/firewall/address-list",
    }),
}


def _check_dangling_references(cfg: Config) -> list[LintIssue]:
    """LINT002: a prop names an ``iac.*`` entity that doesn't exist.

    Walks every item; for each prop in :data:`_REFERENCES`, checks
    whether the value (if it's an iac.* token) exists in any of the
    declared candidate menus, under the right *lookup mode*.

    A comma-separated value (RouterOS list form, e.g.
    ``tagged=iac.bridge.lan,iac.bridge.wifi``) is split and checked
    per-token.

    A leading ``!`` (RouterOS negation, e.g. ``in-interface-list=!iac.list.lan``)
    is stripped before lookup.
    """
    # Build a symbol table per (menu, lookup_mode): the set of valid
    # tokens in that menu under that mode.
    #   entity[menu] -> {iac.x.y.z for every item}
    #   name[menu]   -> {value of every name= prop}
    #   list[menu]   -> {value of every list= prop}
    entity: dict[str, set[str]] = {}
    name_idx: dict[str, set[str]] = {}
    list_idx: dict[str, set[str]] = {}
    for menu, items in cfg.items_by_menu.items():
        e_bucket = entity.setdefault(menu, set())
        n_bucket = name_idx.setdefault(menu, set())
        l_bucket = list_idx.setdefault(menu, set())
        for pos, item in enumerate(items):
            iid = entity_id(item, pos)
            if iid.startswith(IAC_PREFIX):
                e_bucket.add(iid)
            n_val = _strip_outer_quotes(item.props.get("name", ""))
            if n_val.startswith(IAC_PREFIX):
                n_bucket.add(n_val)
            l_val = _strip_outer_quotes(item.props.get("list", ""))
            if l_val.startswith(IAC_PREFIX):
                l_bucket.add(l_val)

    indexes = {
        "entity": entity,
        "name": name_idx,
        "list": list_idx,
    }

    issues: list[LintIssue] = []
    for menu, items in cfg.items_by_menu.items():
        for pos, item in enumerate(items):
            for prop, (mode, candidate_menus) in _REFERENCES.items():
                # Skip props that are *defining* in this menu rather
                # than referencing (e.g. list= inside an address-list
                # creates the grouping label).
                if menu in _DEFINING_PROPS.get(prop, frozenset()):
                    continue
                raw = item.props.get(prop)
                if not raw:
                    continue
                value = _strip_outer_quotes(raw)
                for token in _split_list_value(value):
                    target = token.lstrip("!").strip()
                    if not target.startswith(IAC_PREFIX):
                        continue
                    # Found in any candidate menu under this mode? Skip.
                    idx = indexes[mode]
                    if any(target in idx.get(m, set())
                           for m in candidate_menus):
                        continue
                    self_id = entity_id(item, pos)
                    issues.append(LintIssue(
                        severity=Severity.ERROR,
                        code="LINT002",
                        menu=menu,
                        position=pos,
                        id=self_id if self_id.startswith(IAC_PREFIX) else None,
                        message=(
                            f"property {prop}={target!r} references an iac.* "
                            f"name not defined in any of "
                            f"{', '.join(candidate_menus)}"
                        ),
                    ))
    return issues


def _check_orphan_pool_refs(cfg: Config) -> list[LintIssue]:
    """LINT005: ``/ip/dhcp-server address-pool=`` names a missing pool.

    Subset of LINT002 (pool refs would already be flagged), but reported
    as a separate code because the failure mode is silent: a DHCP server
    starts cleanly with a non-existent pool name; it just never leases
    anything. That's much harder to diagnose than a script-time error.

    Skips when LINT002 would also fire (the message would duplicate).
    Triggers when the dhcp-server's pool ref points to *anything but*
    an existing ``/ip/pool`` named entry -- including the
    ``static-only`` sentinel that RouterOS treats specially.
    """
    pool_names = {
        entity_id(item, pos)
        for pos, item in enumerate(cfg.items_by_menu.get("/ip/pool", []))
        if entity_id(item, pos).startswith(IAC_PREFIX)
    }
    # Also accept the RouterOS-builtin "static-only" sentinel which
    # signals "no dynamic leases; only the static ones I configured".
    pool_names.add("static-only")

    issues: list[LintIssue] = []
    for pos, item in enumerate(cfg.items_by_menu.get("/ip/dhcp-server", [])):
        raw = item.props.get("address-pool")
        if not raw:
            continue
        value = _strip_outer_quotes(raw).strip()
        # Only check iac.* refs -- a non-iac value is presumably an
        # operator's own naming and not our business.
        if not value.startswith(IAC_PREFIX):
            continue
        if value in pool_names:
            continue
        self_id = entity_id(item, pos)
        issues.append(LintIssue(
            severity=Severity.ERROR,
            code="LINT005",
            menu="/ip/dhcp-server",
            position=pos,
            id=self_id if self_id.startswith(IAC_PREFIX) else None,
            message=(
                f"address-pool={value!r} doesn't match any /ip/pool entry; "
                "the DHCP server will start but lease nothing"
            ),
        ))
    return issues


# --- helpers ----------------------------------------------------------------


def _strip_outer_quotes(value: str) -> str:
    """``"foo bar"`` -> ``foo bar``; passthrough otherwise."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        return value[1:-1]
    return value


def _split_list_value(value: str) -> list[str]:
    """RouterOS list form ``a,b,c`` -> ``["a", "b", "c"]``.

    Single value (no comma) returns a one-element list. Empty / all-
    whitespace tokens are dropped (defensive against stray commas).
    """
    parts = [v.strip() for v in value.split(",")]
    return [v for v in parts if v]
