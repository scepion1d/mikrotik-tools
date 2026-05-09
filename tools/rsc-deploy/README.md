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

```powershell
..\..\bin\rsc-deploy.cmd --src ..\..\rsc                        # upload everything (recursive)
..\..\bin\rsc-deploy.cmd --src ..\..\rsc --dry-run              # show what would happen
..\..\bin\rsc-deploy.cmd --src ..\..\out\<bundle>.rsc           # upload one file
..\..\bin\rsc-deploy.cmd --src ..\..\rsc --no-clean             # additive (don't drop previous .rsc)
```

`--src` accepts a file or directory. Dirs walked recursively, files uploaded by basename (flat-flash convention).

## Library

```python
from pathlib import Path
from rsc_deploy import Settings, deploy, load_env

settings = load_env(Path(".env"))
deploy(src=Path("rsc"), settings=settings, dry_run=False, clean=True)
```

## Caveats

- **Password auth only.** No key auth.
- **No host-key verification** (TOFU via `AutoAddPolicy`). Don't use over untrusted networks.
- **`--clean` deletes every `*.rsc` on flash root.** Backups (type=`backup` in `/file/print`) are filtered out.
- **No apply trigger.** After upload, run `/import file-name=...` manually.
- **Network code untested.** Tests cover `.env` parsing only.
