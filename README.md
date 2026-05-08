# mikrotik

Personal RouterOS configuration and tooling for a MikroTik **C53UiG+5HPaxD2HPaxD** running RouterOS 7.20+.

Treats the router config as code: a small set of `.rsc` files defines the entire device state and is applied through a single bootstrap script. A separate Python tool diffs configs into apply-able patches.

## Layout

```
mikrotik/
├── rsc/                    # router-side: .rsc artifacts deployed to the router
│   ├── apply-full.rsc      # entry point — validates + imports modules in order
│   ├── vars.rsc            # non-sensitive tunables (router name, allow-list)
│   ├── secrets.rsc         # credentials — gitignored
│   ├── helpers/            # reusable script helpers
│   │   ├── log.rsc         # iacLog* — structured terminal + /log output
│   │   └── parse.rsc       # iacParseCheck — :parse-based syntax check
│   └── modules/            # network configuration, split by area
│       ├── 10-interfaces.rsc   # ethernet + bridge + bridge ports + lists
│       ├── 20-wifi.rsc         # wifi datapath / security / channels / cfg / hw
│       ├── 30-ip.rsc           # list members, addresses, dhcp, dns
│       ├── 40-firewall.rsc     # nat + filter (v4 + v6)
│       ├── 50-services.rsc     # mac-server, neighbor, ip/service hardening
│       └── 60-system.rsc       # clock, identity, disk, users, scripts, buttons
│
├── tools/                  # dev-side: tooling that runs on your laptop
│   └── rsc-diff/           # Python: diff two .rsc configs into add/set/remove ops
│
├── bin/                    # gitignored — winbox.exe, netinstall, etc.
└── .gitignore
```

Mental model: **`rsc/`** is what runs on the router. **`tools/`** is what runs on your laptop.

> RouterOS flash is flat — the `helpers/` and `modules/` folders only exist on disk for organisation. All 11 `.rsc` files upload to the flash root and reference each other by basename.

## Quick start

### Apply the configuration

Upload all 11 `.rsc` files (2 helpers + 6 modules + apply-full + vars + secrets) to the router's flash root, then in Winbox terminal:

```routeros
# one-time per session: load helpers
/import file-name=log.rsc
/import file-name=parse.rsc

# full apply
/import file-name=apply-full.rsc

# or dry-run (validates everything, skips main.rsc import)
:global iacDryRun true
/import file-name=apply-full.rsc
```

For a fresh-from-factory bootstrap:

```routeros
/system/reset-configuration no-defaults=yes skip-backup=yes \
    run-after-reset=apply-full.rsc
```

(All files must be on flash *before* the reset; the helpers issue inside `apply-full.rsc` will fail if you haven't preloaded them — see `apply-full.rsc` header for the workaround.)

### Diff configs

```powershell
cd tools\rsc-diff
uv sync                  # one-time
uv run rsc-diff old.rsc new.rsc                 # patch to stdout
uv run rsc-diff old.rsc new.rsc -o patch.rsc    # patch to file
uv run rsc-diff old.rsc new.rsc --check         # exit 1 on drift (CI)
```

Examples:

```powershell
# self-check current main.rsc against itself
uv run rsc-diff ..\..\rsc\main.rsc ..\..\rsc\main.rsc

# compare current main.rsc against the previous git revision
git show HEAD~1:rsc/main.rsc > old.rsc
uv run rsc-diff old.rsc ..\..\rsc\main.rsc -o patch.rsc
```

See [tools/rsc-diff/README.md](tools/rsc-diff/README.md) for the library API and roadmap.

## Conventions

### Naming (`iac.<type>.<id>`)

Every config item has a stable identifier so configs can be diffed and patched without fragile position-based matching.

| Item kind | `name` field set? | Identity carrier |
|---|---|---|
| `/interface/list`, `/interface/bridge`, `/interface/wifi/{datapath,security,channel,configuration}`, `/ip/pool`, `/ip/dhcp-server`, `/system/script` | yes | `name=iac.<type>.<id>` |
| `/ip/firewall/*`, `/ipv6/firewall/*`, `/ip/dhcp-server/lease` | no | `comment` starts with `iac.<type>.<id>` |
| Built-ins (`ether1..5`, `wifi1..2`) | n/a | `[find default-name=...]` |
| Singletons (`/ip/dns`, `/system/clock`, ...) | n/a | menu path itself |

`<id>` is a number when items are interchangeable, or a short tag (`wan`/`lan`/`5g`/`2g`) when role is fixed.

### File responsibilities

| File | Responsibility | Commit? |
|---|---|---|
| `rsc/apply-full.rsc` | Orchestrator — preflight (presence + parse), import all in dependency order | yes |
| `rsc/vars.rsc` | Non-sensitive tunables (`adminCidrs`, `routerName`); self-validates | yes |
| `rsc/secrets.rsc` | Credentials (`adminPass`, `wifiIntPass`); self-validates | **no** (gitignored) |
| `rsc/helpers/*.rsc` | Reusable helper functions (log + parse) | yes |
| `rsc/modules/10-interfaces.rsc` | Ethernet + bridge + bridge ports + interface lists | yes |
| `rsc/modules/20-wifi.rsc` | Wi-Fi datapath / security / channels / cfg / hardware bind | yes |
| `rsc/modules/30-ip.rsc` | List membership + IP addr + DHCP server/client + DNS | yes |
| `rsc/modules/40-firewall.rsc` | NAT + filter (IPv4 + IPv6) + IPv6 bogon list | yes |
| `rsc/modules/50-services.rsc` | MAC-server, neighbor, `/ip/service` hardening | yes |
| `rsc/modules/60-system.rsc` | Clock, identity, disk, users, scripts, buttons | yes |

## Hardware

- Device: MikroTik **C53UiG+5HPaxD2HPaxD** (a.k.a. "Chateau LTE6 ax")
- RouterOS 7.20.7+ (uses `/interface/wifi/*` — not the older `/interface/wireless/*`)
- 5 GbE ports (`ether1..ether5`), 5 GHz Wi-Fi 6, 2.4 GHz Wi-Fi 6
- LTE modem (not configured here)

## Network

```
WAN:  ether1                                   (DHCP client)
LAN:  ether2..ether5 + wifi1 + wifi2 (bridge)  192.168.10.0/24
        .1                router
        .2 .. .249        reserved (manual static-leases)
        .250 .. .254      DHCP onboarding pool
```

## Recovery

If apply goes wrong and you can't reach the router by IP:

1. **MAC-Winbox** to the bridge MAC printed on the device label (LAN-side only by default).
2. Check what happened: `/log print where message~"iac\\."`
3. Roll back: `/import file-name=base.rsc` (the original RouterOS defconf, kept as a known-good baseline).
4. Worst case: hold the reset button on power-up for hard factory reset.
