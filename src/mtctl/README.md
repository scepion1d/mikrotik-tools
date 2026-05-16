# mtctl

paramiko-based SSH/SFTP utility for RouterOS. Six subcommands:
**upload** (local → router), **download** (router → local),
**backup** (trigger router-side snapshot of state), **export**
(stream `/export` over SSH stdout to a local file -- read-only,
cron-safe), **import** (run `/import file-name=...` on the
router; also `--validate` to probe without executing), and
**rm** (delete one remote file). Reads connection details from
`.env`. File transfers always overwrite the destination and
auto-create missing parent directories.

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
usage: mtctl {upload,download,backup,export,import,rm} ...
```

### upload

```text
mtctl upload --src LOCAL --dst REMOTE [--env ENV] [--dry-run] [-v]

  --src SRC      local source file path (must exist)
  --dst DST      remote destination path (POSIX, relative to flash root)
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

Example:

```powershell
mtctl upload --src .\out\down.rsc --dst staged/2026-05-11/apply.rsc -v
```

### download

```text
mtctl download --src REMOTE --dst LOCAL [--env ENV] [--dry-run] [-v]

  --src SRC      remote source path (POSIX, relative to flash root)
  --dst DST      local destination file path
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

Example:

```powershell
mtctl download --src staged/2026-05-11/apply.rsc --dst .\out\fetched.rsc -v
```

### backup

```text
mtctl backup [--password PW | --no-encrypt] [--env ENV] [--dry-run] [-v]

  --password PW  encrypt the .backup file with this password
  --no-encrypt   explicitly request an unencrypted .backup (default)
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

Triggers two RouterOS commands and produces a fresh, timestamped folder
on flash:

- `/system/backup save name=backups/<timestamp>/live ...`
  -> `backups/<timestamp>/live.backup` (binary, restorable via
  `/system/backup load`).
- `/export show-sensitive file=backups/<timestamp>/live`
  -> `backups/<timestamp>/live.rsc` (text, restorable via
  `/import file-name=...`).

`<timestamp>` is `YYYYMMDD-HHMMSS` in UTC. The folder path is printed on
stdout for chaining (e.g. `for /f`, PowerShell pipelines).

Examples:

```powershell
# Take an unencrypted snapshot, then download both files for archival.
$folder = mtctl backup --no-encrypt -v
mtctl download --src "$folder/live.backup" --dst ".\out\$folder\live.backup"
mtctl download --src "$folder/live.rsc"    --dst ".\out\$folder\live.rsc"

# Encrypted backup (the .rsc export is always plaintext on flash).
mtctl backup --password "$env:BACKUP_PW" -v
```

> **Security:** `live.rsc` is generated with `show-sensitive` -- it
> contains plaintext PSKs, admin passwords, and any other secrets
> RouterOS tracks. Treat the folder (and any local copy) as secret
> material; don't commit it.

### export

```text
mtctl export --dst LOCAL [--no-sensitive] [--env ENV] [--dry-run] [-v]

  --dst LOCAL    local destination file path (typically .rsc)
  --no-sensitive omit `show-sensitive` so PSKs / passwords come back as placeholders
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

Lightweight, read-only alternative to `backup`. Runs `/export
show-sensitive` on the router and captures stdout via SSH; **writes
nothing to the router's flash**. Use this when:

- Running on a schedule (cron / Windows Task Scheduler) -- avoids the
  forever-growing `backups/<ts>/` tree.
- Comparing live vs candidate (see `drift.ps1` at the iac repo root)
  without altering router state.
- Pulling a one-off config view for a manual diff.

When you need a recoverable snapshot (e.g. before deploy), use
`backup` -- its `.backup` binary is the only thing `/system/backup
load` can fully restore.

Examples:

```powershell
# Quick on-demand snapshot.
mtctl export --dst .\out\live.rsc -v

# Redacted (no secrets) for sharing.
mtctl export --dst .\out\redacted.rsc --no-sensitive
```

Prints the local path on stdout so the command chains nicely:

```powershell
$snap = mtctl export --dst .\out\live.rsc
rsc diff --old $snap --new .\out\candidate.rsc --check
```

> Same security note as `backup`: `show-sensitive` output contains
> plaintext secrets. Treat the local file as sensitive material.

### import

```text
mtctl import --src REMOTE [--quiet] [--validate] [--env ENV] [--dry-run] [-v]

  --src REMOTE   remote .rsc path (POSIX, relative to flash root)
  --quiet        omit verbose=yes (router won't echo each script line)
  --validate     probe the file on the router (exists / size; :parse for
                 small files) without running /import. Mutually exclusive
                 with --dry-run; intended for `deploy.ps1 -DryRun`.
  --env ENV      path to .env file (default: walk up from cwd looking for .env)
  --dry-run      report what would happen without touching the router
  -v, --verbose  -v INFO logs (default WARNING); -vv DEBUG
```

Runs `/import file-name=<src> verbose=yes` on the router against a
previously-uploaded `.rsc` script. The file must already exist on flash;
use `mtctl upload` first if it doesn't. RouterOS reports script errors
on stdout as `failure: ...`; any such line (or a non-zero exit status)
makes this command exit `1`.

`--validate` is the closest thing RouterOS allows to a "parse without
execute" check on `/import`. It opens the SSH session, confirms the
file is on flash via `/file find`, captures its size, and -- for files
under ~3 KB -- runs `:parse [/file get name=<src> contents]` so the
router's own parser verifies syntax. Larger files (our typical 16-22 KB
bundles) get the transport + existence checks only; syntax errors
would surface at the real `/import`. Used by `deploy.ps1 -DryRun` in
the upload + validate + rm chain.

Examples:

```powershell
# Stage and apply a rollforward patch.
mtctl upload --src .\out\deploy\<ts>\up.rsc --dst deployment/<ts>/up.rsc
mtctl import --src deployment/<ts>/up.rsc -v

# Dry run: print the command without touching the router.
mtctl import --src deployment/<ts>/up.rsc --dry-run

# Probe: confirm the router accepts the file without applying.
mtctl upload   --src up.rsc --dst tmp/probe.rsc
mtctl import   --src tmp/probe.rsc --validate
mtctl rm       --path tmp/probe.rsc
```

### rm

```text
mtctl rm --path REMOTE [--env ENV] [--dry-run] [-v]

  --path REMOTE  remote file path (POSIX, relative to flash root)
  --env ENV      path to .env file
  --dry-run      log without connecting
  -v, --verbose  -v INFO logs; -vv DEBUG
```

Delete one remote file via SFTP. Not recursive; for that, drop into
SSH and use `/file remove`. Primary user is `deploy.ps1 -DryRun`,
which uploads a probe / runs `--validate` / cleans up via `mtctl rm`.

## Behaviour

In both directions:

- `--src` and `--dst` are mandatory.
- Missing parent directory of `--dst` is **created** (recursive `mkdir`).
- Existing file at `--dst` is **overwritten**.
- Single file only -- looping over many files is the caller's job.

## Library

```python
from pathlib import Path
from mtctl import Settings, create_backup, download, load_env, upload

settings = load_env(Path(".env"))

upload(Path("out/down.rsc"), "staged/apply.rsc", settings)
download("staged/apply.rsc", Path("out/fetched.rsc"), settings)

folder = create_backup(settings)               # backups/<timestamp>/
download(f"{folder}/live.backup", Path(f"out/{folder}/live.backup"), settings)
download(f"{folder}/live.rsc",    Path(f"out/{folder}/live.rsc"),    settings)
```

## Architecture

Three layers, each with its own exception:

| Module                                            | Responsibility                                                  |
| ------------------------------------------------- | --------------------------------------------------------------- |
| `mtctl.ssh.SshSession` / `SshError`          | One paramiko `SSHClient` connection lifecycle (TOFU host keys); `open_sftp()` + `exec()`. |
| `mtctl.sftp.SftpClient` / `SftpError`        | One SFTP channel: `listdir`, `remove`, `ensure_dir`, `put`, `get`, `stat_size`. |
| `mtctl.deployer.upload` / `download`         | Single-file transfer orchestrators: validate inputs, ensure destination dir, transfer one file. |
| `mtctl.backup.create_backup` / `BackupError` | Triggers `/system/backup save` + `/export show-sensitive` into `backups/<timestamp>/`. |
| `mtctl.importer.run_import` / `ImportError` | Triggers `/import file-name=<remote>` against a router-side `.rsc`. |

The CLI is a thin wrapper that builds a `Settings` from `.env` and
dispatches one orchestrator call.

## Known limits

- **Password auth only.** No key auth.
- **No host-key verification** (TOFU via `AutoAddPolicy`). Don't use over untrusted networks.
- **No apply trigger.** After uploading an `.rsc`, run `/import file-name=…` manually on the router.
- **Single file at a time.** Composing batches is left to the caller.
- **`backup` doesn't auto-download.** It only creates the snapshot on flash; pull both files with `download` if you want off-device archival.
- **User must have backup + sensitive policies.** The `iac.user` group needs `read,write,sensitive,ssh` (the default `full` group already has them).
