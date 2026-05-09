# rsc-deploy

Lightweight RouterOS `.rsc` deployer. Reads connection details from `.env`,
connects via SSH/SFTP, drops the previous on-flash `.rsc` state, and uploads
the current set from a source path.

> ⚠️ **Status: MVP, currently blocked.**
> Code is in place (paramiko-based SSH+SFTP, `--src` / `--dry-run` /
> `--no-clean`, `.env` loader), but real deploys against the test router fail
> with `Error reading SSH protocol banner` -- RouterOS accepts the TCP
> connection then closes before sending the SSH banner. The cause hasn't
> been identified yet (firewall and `/ip/service` config look correct;
> host-key regen + service toggle didn't help, OpenSSH sees the same
> behaviour). Until this is resolved, upload via Winbox Files panel
> (drag the bundled `.rsc` onto the panel, then `/import file-name=...`).
>
> See [`ROADMAP.md`](ROADMAP.md) for next steps once unblocked.

## Why a separate tool

`rsc-bundle` and `rsc-diff` are pure-text tools that don't touch the network.
Deploy is the one operation that talks to a live router, so it lives in its
own package with its own (small) dependency surface.

## Configuration

Create `.env` at the **repo root** (gitignored). Example values:

```ini
# .env
ROUTER_HOST=192.168.10.1
ROUTER_USER=admin
ROUTER_PASSWORD=changeme
ROUTER_PORT=22                # optional, default 22
ROUTER_TIMEOUT=10             # seconds, optional, default 10
```

A starter template lives at `.env.example` (committed) — copy it:

```powershell
Copy-Item .env.example .env
notepad .env
```

## Install

One runtime dep (`paramiko` for SSH/SFTP); Python ≥ 3.10.

```powershell
cd tools\rsc-deploy
uv sync
```

## CLI

```powershell
# upload everything in rsc/ (recursively, flattened to flash root)
uv run rsc-deploy --src ..\..\rsc

# dry-run -- show what would be deleted + uploaded, do nothing
uv run rsc-deploy --src ..\..\rsc --dry-run

# upload a single file (e.g. a bundled output)
uv run rsc-deploy --src ..\..\out\apply.rsc

# skip the "drop previous state" step (additive upload)
uv run rsc-deploy --src ..\..\rsc --no-clean
```

`--src` accepts a file or a directory. Directories are walked recursively;
files are uploaded by basename (matches the flat-flash convention).

## Library

```python
from pathlib import Path
from rsc_deploy import Settings, deploy, load_env

settings = load_env(Path(".env"))
deploy(src=Path("rsc"), settings=settings, dry_run=False, clean=True)
```

### Public API

| Symbol | Purpose |
|---|---|
| `load_env(path)` | Parse a `.env` file → `Settings` |
| `Settings` | Connection settings dataclass |
| `deploy(src, settings, ...)` | Connect, optionally clean, upload |
| `DeployError` | Raised on connection / transfer failures |
| `__version__` | Package version |

## Layout

```
tools/rsc-deploy/
├── README.md
├── ROADMAP.md
├── pyproject.toml
├── rsc_deploy/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py             # .env loader + Settings
│   ├── deployer.py           # connect + clean + upload (paramiko)
│   └── py.typed
└── tests/
    └── test_config.py        # .env parsing only (network code untested)
```

## Caveats (MVP)

- **Password auth only.** Key-based auth is on the roadmap.
- **No host-key verification.** Uses `AutoAddPolicy` (TOFU). Don't use over
  untrusted networks. Hardening is on the roadmap.
- **`--clean` is destructive.** It deletes every `*.rsc` on flash root.
  Backup files on the router are NOT touched (they live in `/file/print` as
  type `backup`, but rsc-deploy filters by `.rsc` extension explicitly).
- **No apply trigger.** After upload you still run `/import file-name=apply.rsc`
  in Winbox/SSH manually. A `--apply` flag is on the roadmap.
- **Network code is untested.** Tests cover `.env` parsing only — real
  end-to-end testing requires a router (or a docker RouterOS image).
