# mikrotik

Personal RouterOS IaC for a **MikroTik C53UiG+5HPaxD2HPaxD** on RouterOS 7.20+.
Modular `.rsc` source under `rsc/`, three Python tools under `tools/` to bundle / diff / deploy.

## Layout

```
mikrotik/
├── rsc/                     source of truth (one folder per "site")
│   ├── <config-name>/       router configuration
│   ├── ...
├── tools/
│   ├── rsc-bundle/          inline /imports + flatten scripting -> single .rsc
│   ├── rsc-diff/            file-vs-file differ + verifier
│   └── rsc-deploy/          SSH/SFTP uploader (BLOCKED, see below)
├── out/                     gitignored: bundles, patches, live exports
├── bin/                     gitignored: tool shims (.cmd / symlink)
└── build.ps1                sync all tools, refresh shims in bin/
```

Each site under `rsc/<site>/` has `<site>.rsc` (entry), `vars.rsc` + `secrets.rsc` (gitignored), `helpers/`, `modules/NN-*.rsc` (numeric apply order).

## End-to-end flow

```powershell
# 1. one-time: build tools, drop shims into bin/
.\build.ps1

# 2. bundle a site -> single self-contained .rsc in out/
.\bin\rsc-bundle.cmd --mainScript rsc\<site>\<site>.rsc --out out
# -> out\<site>-YYMMDD-XXXXX.rsc   (no /import lines, vars resolved, scripting stripped)

# 3. capture live router state
#    (in Winbox terminal): /export terse file=live
#    drag /file/live.rsc off the router into out\live.rsc

# 4. compute rollforward + rollback patches
$candidate = (Get-ChildItem out\<site>-*.rsc | Sort Name | Select -Last 1).FullName
.\bin\rsc-diff.cmd out\live.rsc $candidate --lenient -o out\rollforward.rsc
.\bin\rsc-diff.cmd $candidate out\live.rsc --lenient -o out\rollback.rsc

# 5. semantic round-trip check
.\bin\rsc-diff-verify.cmd --lenient
# OK on both legs = patches transform live <-> candidate cleanly

# 6. apply on router (manual until rsc-deploy is unblocked)
#    upload out\rollforward.rsc via Winbox Files panel, then in terminal:
#    /import file-name=rollforward.rsc
```

For a fresh device install, use the bundle directly:

```routeros
/system/reset-configuration no-defaults=yes skip-backup=yes \
    run-after-reset=<site>-260509-XXXXX.rsc
```

## Conventions

### Naming (`iac.<type>.<id>`)

Every config item carries a stable identifier so configs can be diffed and patched without position-based matching.

| Item kind | Identity carrier |
|---|---|
| Items with `name=` field | `name=iac.<type>.<id>` |
| Items without `name=` (firewall rules, leases, IPv6 list entries) | `comment` begins with `iac.<type>.<id>` |
| Built-ins being renamed (`etherN` / `wifiN`) | `[find default-name=...]` **only at the rename point** |
| Singletons (`/ip/dns`, `/system/clock`, `/disk/settings`, ...) | the menu path |

### File responsibilities

| File | Commit? |
|---|---|
| `<site>.rsc` — orchestrator | yes |
| `vars.rsc` — non-sensitive tunables | yes |
| `secrets.rsc` — credentials | **no** (gitignored) |
| `helpers/*.rsc` — reusable functions | yes |
| `modules/NN-area.rsc` — config slices in numeric order | yes |

`secrets.rsc` and `vars.rsc` self-validate at import via `$iacLogError`.

## Recovery

If the router becomes unreachable:

1. **MAC-Winbox** to the bridge MAC printed on the device label (LAN-only by default).
2. `/log print where message~"iac\\."` — see what the orchestrator did.
3. `/system/reset-configuration` — back to RouterOS defconf.
4. Hold the reset button on power-up for hard factory reset.

## Status / known issues

| Area | State |
|---|---|
| `rsc-bundle` | ✅ working. Inlines imports, unfolds `:foreach`, resolves `:global` vars, strips scripting wrappers, normalizes quoting to match `/export` style. |
| `rsc-diff` | ✅ usable for live-vs-candidate. Identity-based matching; defaults table for known props; `--strict` (no normalization) and `--lenient` (treat explicit-neutral-vs-missing as equal) flags. |
| `rsc-diff-verify` | ✅ working. Re-runs the differ on `apply_patch(live, fwd) == candidate` to score round-trip. |
| `rsc-deploy` | ❌ blocked. paramiko fails with `Error reading SSH protocol banner` against this router; root cause unknown. Use Winbox Files panel + terminal `/import` instead. |
| `/ip/service` positional matching | Open. Differ falls back to `@anon=N` for unnamed singleton-set items, which mis-aligns when live and candidate emit them in different orders. `--lenient` papers over the symptom for now. |
| RouterOS expression literals (`admin-mac=[/interface/ethernet get ...]`) | Open. Differ stores the expression as a literal string; live router evaluates it. Round-trips clean only because both sides agree on the literal. |
| Authored configs vs `/export` ordering | The bundler doesn't reorder items to match export's alphabetized property output. Differ tolerates this via identity matching. |

See per-tool READMEs for tool-level caveats.
