# mikrotik

Personal RouterOS configuration and tooling for a MikroTik **C53UiG+5HPaxD2HPaxD** running RouterOS 7.20+.

Treats the router config as code: a small set of `.rsc` files defines the entire device state, and a `rsc-bundle` tool inlines them into a single self-contained `.rsc` for `/import` on the router.

## Layout

```
mikrotik/
├── rsc/                          # router-side: .rsc artifacts
│   └── base/                     # one config "site" (more later for fleets)
│       ├── base.rsc              # entry point — imports everything below
│       ├── vars.rsc              # non-sensitive tunables (router name, allow-list)
│       ├── secrets.rsc           # credentials — gitignored
│       ├── helpers/
│       │   └── log.rsc           # iacLog* — structured terminal + /log output
│       └── modules/
│           ├── 10-interfaces.rsc # ethernet + bridge + bridge ports + lists
│           ├── 20-wifi.rsc       # wifi datapath / security / channels / cfg / hw
│           ├── 30-ip.rsc         # list members, addresses, dhcp, dns
│           ├── 40-firewall.rsc   # nat + filter (v4 + v6)
│           ├── 50-services.rsc   # mac-server, neighbor, ip/service hardening
│           └── 60-system.rsc     # clock, identity, disk, users, scripts, buttons
│
├── tools/                        # dev-side: tooling that runs on your machine
│   ├── rsc-bundle/               # ✅ inline /imports into one .rsc — primary build step
│   ├── rsc-diff/                 # ⚠️  diff two configs into add/set/remove ops (MVP)
│   └── rsc-deploy/               # ⚠️  SSH/SFTP upload (MVP, blocked by SSH banner-timeout)
│
├── out/                          # gitignored build output (timestamped bundles)
├── bin/                          # gitignored — winbox.exe + tool shims (.cmd / symlink)
├── build.ps1                     # sync all tools, link shims into bin/
├── .env                          # template for rsc-deploy credentials
└── .gitignore
```

Mental model: **`rsc/`** is the source of truth for what runs on the router. **`tools/`** is what runs on your laptop to build/inspect/deploy that source. **`out/`** holds the built artifact (one timestamped `.rsc`).

## Workflow: bundle → upload → apply

The current happy path is **bundle on laptop, single-file upload to router**:

```powershell
# 1. one-time setup
.\build.ps1                         # uv sync each tool, shim into bin/

# 2. build the bundle
.\bin\rsc-bundle.cmd --mainScript rsc\base\base.rsc --out out
# -> out\base-260509-XXXXX.rsc  (one self-contained .rsc; helpers + modules + secrets + vars all inlined)

# 3. upload that single file to the router via Winbox Files panel

# 4. on the router (Winbox terminal):
/import file-name=base-260509-XXXXX.rsc
# or for a fresh install via reset:
/system/reset-configuration no-defaults=yes skip-backup=yes \
    run-after-reset=base-260509-XXXXX.rsc
```

The bundler:

- Resolves each `/import file-name=helpers/log.rsc` **relative to the importing file's directory**
- Unfolds `:foreach f in=$iacFiles do={ /import file-name=$f }` over `:local`/`:global` string-literal arrays so dynamic imports become literal
- Preserves variable-target imports (`/import file-name=$something`) verbatim
- Detects cycles, errors on missing targets

See [tools/rsc-bundle/README.md](tools/rsc-bundle/README.md) for library API and roadmap.

## Conventions

### Naming (`iac.<type>.<id>`)

Every config item carries a stable identifier so configs can be diffed,
patched, and cross-referenced without fragile position-based matching.

| Item kind | Identity carrier |
|---|---|
| Items the menu lets you `name=` (interfaces you create, security/datapath/channel/configuration profiles, pools, DHCP servers, scripts, ...) | `name=iac.<type>.<id>` |
| Items that have no `name=` field (firewall rules, IPv6 list entries, DHCP leases, ...) | `comment` begins with `iac.<type>.<id>` |
| Built-ins you just rename (factory `etherN` / `wifiN`) | `[find default-name=...]` is used **only at the rename point** |
| Singletons (one row per menu, e.g. `/ip/dns`, `/system/clock`, `/disk/settings`, ...) | the menu path itself |

Conventions inside the id:

- `<type>` is a short descriptor that mirrors the menu (`list`, `bridge`,
  `ether`, `wifi.dp`, `wifi.sec`, `wifi.ch`, `wifi.cfg`, `pool`, `dhcp`,
  `script`, ...). The point is to make the id human-greppable, not to
  duplicate the menu path.
- `<id>` is a number when items are interchangeable, or a short tag when
  the role is fixed (e.g. `wan` / `lan`, frequency band, segment name).

**Selector rule.** The `set` that **assigns** an `iac.*` name to a built-in
addresses it by `[find default-name=...]` (the new name doesn't exist yet).
Every other reference uses the iac name directly -- including
cross-references like `interface=iac.ether.lan1` or
`admin-mac=[/interface/ethernet get [find name=iac.ether.lan1] mac-address]`.

### File responsibilities

| File | Responsibility | Commit? |
|---|---|---|
| `rsc/<site>/base.rsc` | Orchestrator -- imports helpers, globals, modules in order | yes |
| `rsc/<site>/vars.rsc` | Non-sensitive tunables; self-validates on import | yes |
| `rsc/<site>/secrets.rsc` | Credentials; self-validates on import | **no** (gitignored) |
| `rsc/<site>/helpers/*.rsc` | Reusable helper functions (currently: `log.rsc`) | yes |
| `rsc/<site>/modules/NN-area.rsc` | One concern per module, applied in numeric order | yes |

The `NN-` prefix encodes apply order. Gaps of 10 leave room to insert new
concerns without renumbering.

### Validation lives with the data

`secrets.rsc` and `vars.rsc` self-validate immediately after their
assignments using `$iacLogError` (which logs and aborts). There is no
separate validator helper -- if a value is wrong, the import that defined
it fails right there with a clear message, before any later module has a
chance to reference it.

## Hardware-specific bits

Anything that's specific to **this** device, RouterOS version, or network
layout lives in the source files themselves (mainly `rsc/<site>/modules/`
and `rsc/<site>/vars.rsc`), not in this README. The current `base` site
targets a single MikroTik router on RouterOS 7.20+ with one bridged LAN
and a WAN uplink; see the module headers for details.

## Recovery

If apply goes wrong and you can't reach the router by IP:

1. **MAC-Winbox** to the bridge MAC printed on the device label (LAN-only by
   default).
2. Check what happened: `/log print where message~"iac\\."`
3. `/system/reset-configuration` -- back to RouterOS defconf.
4. Worst case: hold the reset button on power-up for hard factory reset.

## Known issues / WIP

| Tool | Status | Notes |
|---|---|---|
| **rsc-bundle** | ✅ working | Used end-to-end. Output file is auto-named `<stem>-<yymmdd>-<seconds-since-midnight>.rsc`. |
| **rsc-diff** | ⚠️ MVP only | Parses + diffs `.rsc` files into add/set/remove ops with iac-aware identity resolution. Known gaps: no property normalisation (so semantically-equal values can show as drift), no ordering-aware moves for ordered menus, no live-router mode. Useful for sanity checks; not yet authoritative. See [tools/rsc-diff/ROADMAP.md](tools/rsc-diff/ROADMAP.md). |
| **rsc-deploy** | ⚠️ blocked | Code is in place (paramiko-based SSH+SFTP, `--src` / `--dry-run` / `--no-clean`), but real deploys currently fail with `Error reading SSH protocol banner` -- the router accepts the TCP then closes before sending the SSH banner. Cause not yet identified. Until resolved, upload via Winbox Files panel. See [tools/rsc-deploy/README.md](tools/rsc-deploy/README.md). |

The diff and deploy tools are scaffolded the same way as rsc-bundle so they
remain ready to harden once the immediate blockers are cleared.

