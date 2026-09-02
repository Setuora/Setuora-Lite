# Setuora Lite

> Windows franchise server with gated Tally debtor/creditor synchronization.

## Overview

Setuora Lite runs on the Windows server inside each franchise. It keeps the
franchise operations application local, talks to the local Tally gateway, and
exchanges only debtor/creditor XML with Setuora Master through an isolated SFTP
account.

The supported deployment is Windows-native. Docker, WSL, Caddy, and Tailscale
are not part of installation or runtime. Historical Linux/container and private
network assets are retained under `archive/` for reference.

## Architecture

```text
Franchise Tally
  -> Setuora Lite exports Sundry Debtors / Sundry Creditors
  -> SFTP /inbox on the Master public endpoint
  -> Setuora Master validates and consolidates parties
  -> SFTP /outbox/setuora-...xml
  -> Setuora Lite imports the XML into local Tally
  -> SFTP /ack/<same-file-stem>.ack
  -> Master accepts the next franchise upload
```

Synchronization is deliberately gated. Lite does not upload a fresh Tally
export while Master has an XML waiting in `/outbox`, and it never creates an
acknowledgement until Tally reports at least one created or altered master.

## Security properties

- The franchise makes outbound SFTP connections only; no public franchise port
  is required.
- Every franchise receives a separate chrooted, SFTP-only Master account.
- Lite pins the exact SHA256 SSH host-key fingerprint before sending a password.
- Uploads use a `.part` suffix and are renamed to `.xml` only when complete.
- Master XML is size-bounded and parsed with entity expansion disabled.
- Exchange history is durable in SQLite and passwords are never shown in the UI.
- The Lite web port is opened only on the Windows Private firewall profile.

## Requirements

- Windows Server 2019+ or a current Windows 10/11 Pro machine
- Python 3.11+ (the installer can add it with WinGet)
- Tally Prime with the local HTTP/XML gateway enabled, normally on port `9000`
- Outbound access to the Master public IP and SFTP port
- The franchise code, isolated SFTP username/password, and verified Master SSH
  host-key fingerprint

## Installation

Build the self-extracting Windows installer:

```powershell
py -3.11 scripts\build_client_packages.py --version 1.0.0
```

Copy `dist\Setuora-Lite-1.0.0-windows.cmd` to the franchise server and run it
as Administrator. It installs under
`C:\ProgramData\Setuora\Setuora-Lite-windows`, creates a private virtual
environment, registers a Windows startup task, creates a Private-profile
firewall rule, and verifies the local health endpoint.

After signing in:

1. Open **Admin → Settings** and confirm the Tally company, host, and port.
2. Open **Admin → Tally SFTP**.
3. Enter the permanent franchise code and the credentials issued by Master.
4. Verify the SHA256 host-key fingerprint out of band.
5. Enable background synchronization and select **Sync now**.

### Windows source-checkout controls

For a Windows source checkout used for development or direct server setup, run
the root `setuora.bat` (not the generated release installer). Double-click it
for a menu, or call it by path from any working directory with one of these
commands:

```bat
setuora.bat setup
setuora.bat start
setuora.bat stop
setuora.bat update
```

Run `setuora.bat setup` first; it requests Administrator approval and installs
Python 3.11 with Windows Package Manager when needed. Setup and update elevate
automatically, wait for the Administrator operation to finish, and report its
result in the original menu or shell. These wrappers find the checkout's
`deploy.py`; they are source-checkout/developer controls.
For a production packaged
installation, continue to use the installer procedure above and its installed
`setuora.ps1` lifecycle script.

## Operations

```powershell
$setuora = "C:\ProgramData\Setuora\Setuora-Lite-windows\setuora.ps1"
& $setuora status
& $setuora logs --follow
& $setuora stop
& $setuora start
```

Run a newer installer to update. The updater preserves `.env`, the database,
backups, and `data\sftp-connection.env`.

## Development and tests

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.lock
copy .env.example .env
python -m pytest -q
```

## Documentation

- [Windows installation](docs/deployment/installation-guide.md)
- [Tally and SFTP operation](docs/deployment/tally-integration-guide.md)
- [Backup and recovery](docs/deployment/backup-restore-guide.md)
- [Release checklist](docs/deployment/production-release-checklist.md)
- [SFTP/Tally architecture](docs/architecture/sftp-tally-topology.md)
