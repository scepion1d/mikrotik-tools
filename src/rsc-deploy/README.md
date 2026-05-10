# rsc-deploy

paramiko-based SSH/SFTP uploader for RouterOS `.rsc` files. Reads connection details from `.env`, optionally cleans previous `.rsc` state from flash, uploads new files.

> ⚠️ **BLOCKED.** Real deploys against the test router fail with `Error reading SSH protocol banner` — RouterOS accepts the TCP connection then closes before sending the SSH banner. `/ip/service` config and host-key regen don't help; OpenSSH sees the same behavior. Use Winbox Files panel + terminal `/import` until resolved.

## Install

```powershell
.\build.ps1            # uv sync (one runtime dep: paramiko)
```

Python ≥ 3.10.

## Configuration

Create `.env` at the **repo root** (gitignored):

```ini
ROUTER_HOST=192.168.10.1
ROUTER_USER=admin
ROUTER_PASSWORD=changeme
ROUTER_PORT=22                # optional, default 22
ROUTER_TIMEOUT=10             # optional, default 10
```

Template at `.env.example`.

## CLI

```text
usage: rsc-deploy [-h] --src SRC [--env ENV] [--dry-run] [--no-clean] [-v]

Upload RouterOS .rsc files over SSH/SFTP.

options:
  -h, --help     show this help message and exit
  --src SRC      source path (a .rsc file or a directory walked recursively)
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  --no-clean     skip deleting existing *.rsc on flash before upload
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

## Library

```python
from pathlib import Path
from rsc_deploy import Settings, deploy, load_env

settings = load_env(Path(".env"))
deploy(src=Path("rsc"), settings=settings, dry_run=False, clean=True)
```

## Known issues

- **BLOCKED end-to-end** (see banner above). Network code untested against a real router; tests cover `.env` parsing only.
- **Password auth only.** No key auth.
- **No host-key verification** (TOFU via `AutoAddPolicy`). Don't use over untrusted networks.
- **`--clean` deletes every `*.rsc` on flash root.** Backups (type=`backup` in `/file/print`) are filtered out.
- **No apply trigger.** After upload, run `/import file-name=…` manually.
