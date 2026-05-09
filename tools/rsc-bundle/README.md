# rsc-bundle

Lightweight RouterOS `.rsc` bundler. Takes one entry script and inlines every
`/import file-name=...` directive — and unrolls every `:foreach` over a known
array binding so dynamic `/import file-name=$f` patterns become resolvable —
to produce a single self-contained `.rsc` file.

Status: **MVP** — basename resolver + cycle detection + `:foreach` unfolder
+ CLI + library API. See [`ROADMAP.md`](ROADMAP.md).

## Why

Several router workflows want a single file:

- `run-after-reset=<one file>` accepts only one filename
- Audit / review (whole config in one place)
- Distribution to a fresh device (one upload, not 11)

## Install

Zero runtime dependencies; Python ≥ 3.10.

```powershell
# from this folder
.\build.ps1

# or globally from repo root (also shims into bin/)
..\..\build.ps1
```

After building, the executable is reachable as `bin\rsc-bundle.cmd` from
the repo root, or directly via `uv run rsc-bundle`.

## CLI

```powershell
# bundle base.rsc into out/, auto-named with timestamp
..\..\bin\rsc-bundle.cmd --mainScript ..\..\rsc\base\base.rsc --out ..\..\out
# -> out\base-260509-XXXXX.rsc   (one self-contained .rsc, no /import lines remain)
```

Flags:

| Flag | Meaning |
|---|---|
| `--mainScript PATH` | Entry `.rsc` file. Its parent directory is the import search root. **Required.** |
| `--out DIR` | Output directory (created if missing). Filename is auto-generated as `<stem>-<yymmdd>-<seconds-since-midnight>.rsc`. **Required.** |

The path on stdout is the new file -- handy for piping into another tool.

## Library

```python
from rsc_bundle import bundle, bundle_file

# from a path -- imports resolved relative to entry's parent dir
text: str = bundle_file("rsc/base/base.rsc")

# from a string ({basename: text} map -- used by tests)
sources = {"a.rsc": "/import file-name=b.rsc\n", "b.rsc": ":log info b\n"}
text = bundle("a.rsc", sources)
```

### Public API

| Symbol | Purpose |
|---|---|
| `bundle_file(entry, root=None)` | Bundle an entry file from disk. `root` is accepted for API compat but ignored — imports are resolved relative to each importing file. |
| `bundle(entry_basename, sources)` | Bundle from an in-memory `{basename: text}` map. Mostly used by tests. |
| `BundleError` | Raised on missing import target / cycle / read failure |
| `__version__` | Package version |

## How import resolution works

Each `/import file-name=TARGET` is resolved **relative to the importing file's
directory**. So inside `rsc/base/base.rsc`:

```routeros
/import file-name=helpers/log.rsc        # -> rsc/base/helpers/log.rsc
/import file-name=modules/10-interfaces.rsc   # -> rsc/base/modules/10-interfaces.rsc
```

This is what the running router will see at apply time too — RouterOS resolves
`/import file-name=PATH` against flash root, and a bundled file just side-steps
the whole "is this file on flash?" question because every target is already
inlined.

## How `:foreach` unfolding works

Apply orchestrators typically iterate a list of imports:

```routeros
:local iacFiles { "secrets.rsc"; "vars.rsc"; "modules/10-interfaces.rsc" }

:foreach f in=$iacFiles do={
    /import file-name=$f
}
```

`rsc-bundle` does a small partial evaluation:

1. Scans for `:local NAME { "a"; "b"; ... }` and `:global NAME { ... }` —
   collects them as bindings (only string-literal arrays).
2. Replaces each `:foreach VAR in=$NAME do={ BODY }` over a known array
   with the body emitted once per item, substituting `$VAR` → `"item"`.
3. The resulting literal `/import file-name="..."` lines are then resolved
   and inlined by the standard bundler walk.

Unknown arrays and non-string arrays are left intact (no broken substitution).

## Layout

```
tools/rsc-bundle/
├── README.md
├── ROADMAP.md
├── build.ps1                  # uv sync wrapper
├── pyproject.toml
├── rsc_bundle/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                 # --mainScript / --out + auto-naming
│   ├── bundler.py             # core: visit graph, inline imports, cycle detect
│   ├── unfold.py              # :foreach + array-binding partial evaluator
│   ├── resolver.py            # resolve_relative(importer, target)
│   └── py.typed
└── tests/
    ├── fixtures/
    │   ├── simple/{entry,inner}.rsc
    │   ├── nested/{a,b,c}.rsc
    │   └── cycle/{a,b}.rsc
    ├── test_bundle.py
    └── test_unfold.py
```

## Caveats (MVP)

- Imports must be on a line of the form `/import file-name=PATH` or
  `/import file-name="PATH"` (optionally indented; trailing whitespace OK).
  Same line must not contain anything else.
- Only `:foreach` over `:local` / `:global` arrays of **string literals**
  are unfolded. Mixed/computed arrays bail and the loop stays intact.
- Imports whose target is a variable reference (`/import file-name=$var`)
  that the unfolder couldn't resolve are passed through verbatim. RouterOS
  will then evaluate them at apply time — only useful if those files
  happen to also be on flash, which defeats the purpose of bundling.