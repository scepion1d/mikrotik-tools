# rsc-deploy

paramiko-based single-file SSH/SFTP transfer for RouterOS. Two subcommands:
**upload** (local → router) and **download** (router → local). Reads
connection details from `.env`. Always overwrites the destination and
auto-creates missing parent directories.

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

## Connectivity

Reaching the router on TCP/22 from your workstation requires a clear
LAN path. Anything that intercepts or tunnels local traffic can prevent
the SSH banner exchange and surface as `ssh connect failed`,
`Error reading SSH protocol banner`, or a plain timeout. Common culprits:

- **Local firewall** (Windows Defender Firewall, third-party endpoint
  protection) blocking outbound SSH or the router's reply.
- **Corporate / personal VPN** routing `192.168.0.0/16` (or your
  router's subnet specifically) over the tunnel instead of the LAN.
- **Microsoft Entra Global Secure Access** (or similar SASE/ZTNA agents)
  capturing all traffic via its forwarding profile -- LAN destinations
  must be added to the **bypass** list, otherwise packets are sent to
  the cloud edge and dropped.
- **Split-DNS / VPN DNS** resolving the router's hostname to an internal
  address you can't reach from the current network.

Quick checks before suspecting the tool:

```powershell
Test-NetConnection 192.168.10.1 -Port 22
ssh -v admin@192.168.10.1                    # OpenSSH gives the same banner errors
```

If `Test-NetConnection` succeeds but the deploy fails, the issue is in
the SSH/SFTP layer; otherwise, it's connectivity.

## CLI

```text
usage: rsc-deploy {upload,download} --src SRC --dst DST [--env ENV] [--dry-run] [-v]
```

### upload

```text
rsc-deploy upload --src LOCAL --dst REMOTE [--env ENV] [--dry-run] [-v]

  --src SRC      local source file path (must exist)
  --dst DST      remote destination path (POSIX, relative to flash root)
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

Example:

```powershell
rsc-deploy upload --src .\out\down.rsc --dst staged/2026-05-11/apply.rsc -v
```

### download

```text
rsc-deploy download --src REMOTE --dst LOCAL [--env ENV] [--dry-run] [-v]

  --src SRC      remote source path (POSIX, relative to flash root)
  --dst DST      local destination file path
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

Example:

```powershell
rsc-deploy download --src staged/2026-05-11/apply.rsc --dst .\out\fetched.rsc -v
```

## Behaviour

In both directions:

- `--src` and `--dst` are mandatory.
- Missing parent directory of `--dst` is **created** (recursive `mkdir`).
- Existing file at `--dst` is **overwritten**.
- Single file only -- looping over many files is the caller's job.

## Library

```python
from pathlib import Path
from rsc_deploy import Settings, download, load_env, upload

settings = load_env(Path(".env"))

upload(Path("out/down.rsc"), "staged/apply.rsc", settings)
download("staged/apply.rsc", Path("out/fetched.rsc"), settings)
```

## Architecture

Three layers, each with its own exception:

| Module                                     | Responsibility                                                  |
| ------------------------------------------ | --------------------------------------------------------------- |
| `rsc_deploy.ssh.SshSession` / `SshError`   | One paramiko `SSHClient` connection lifecycle (TOFU host keys). |
| `rsc_deploy.sftp.SftpClient` / `SftpError` | One SFTP channel: `listdir`, `remove`, `ensure_dir`, `put`, `get`, `stat_size`. |
| `rsc_deploy.deployer.upload` / `download`  | Orchestrators: validate inputs, ensure destination dir, transfer one file. |

The CLI is a thin wrapper that builds a `Settings` from `.env` and
dispatches one orchestrator call.

## Known limits

- **Password auth only.** No key auth.
- **No host-key verification** (TOFU via `AutoAddPolicy`). Don't use over untrusted networks.
- **No apply trigger.** After uploading an `.rsc`, run `/import file-name=…` manually on the router.
- **Single file at a time.** Composing batches is left to the caller.
