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
cd tools\rsc-bundle
uv sync
```

## CLI

```powershell
# bundle apply.rsc -- the bundler unfolds its :foreach loops automatically
uv run rsc-bundle ..\..\rsc\apply.rsc --root ..\..\rsc -o bundled.rsc

# bundle to stdout
uv run rsc-bundle ..\..\rsc\apply.rsc --root ..\..\rsc
```

`--root` (defaults to entry's parent dir) is walked recursively; basenames
must be unique across the tree.

## Library

```python
from rsc_bundle import bundle, bundle_file

# from a path
text: str = bundle_file("rsc/apply.rsc", root="rsc")

# from a string (provide a {basename: source_text} resolver)
sources = {"a.rsc": "/import file-name=b.rsc\n", "b.rsc": ":log info b\n"}
text = bundle("a.rsc", sources)
```

### Public API

| Symbol | Purpose |
|---|---|
| `bundle_file(entry, root=None)` | Walk `root`, build basename map, bundle `entry` |
| `bundle(entry_basename, sources)` | Bundle from an explicit `{basename: text}` map |
| `BundleError` | Raised on missing import / cycle |
| `__version__` | Package version |

## How `:foreach` unfolding works

Apply orchestrators typically iterate a list of imports:

```routeros
:local iacFiles { "secrets.rsc"; "vars.rsc"; "10-interfaces.rsc" }

:foreach f in=$iacFiles do={
    /import file-name=$f
}
```

`rsc-bundle` does a small partial evaluation:

1. Scans for `:local NAME { "a"; "b"; ... }` and `:global NAME { ... }` —
   collects them as bindings (only string-literal arrays).
2. Replaces each `:foreach VAR in=$NAME do={ BODY }` over a known array
   with the body emitted once per item, substituting `$VAR` → `"item"`.
3. The resulting literal `/import file-name="…"` lines are then inlined
   by the standard bundler walk.

Unknown arrays and non-string arrays are left intact (no broken substitution).

## Layout

```
tools/rsc-bundle/
├── README.md
├── ROADMAP.md
├── pyproject.toml
├── rsc_bundle/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── bundler.py            # core: visit graph, inline imports
│   ├── unfold.py             # :foreach + array-binding partial evaluator
│   ├── resolver.py           # walk root -> basename -> path
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

- Imports must be on a line of the form `/import file-name=NAME.rsc` or
  `/import file-name="NAME.rsc"` (optionally indented; trailing whitespace OK).
- Only `:foreach` over `:local` / `:global` arrays of **string literals**
  are unfolded. Mixed/computed arrays bail and the loop stays intact.
- Bundled output preserves runtime checks like `$iacParseCheck` calls. If
  your apply script does file-presence checks, they will fail when run from
  the bundled file (the originals aren't on flash anymore). Either remove
  those checks for the bundled path or make them bundle-aware via a flag.
