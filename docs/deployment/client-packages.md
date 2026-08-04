# Shareable Linux and Windows Client Packages

Setuora Lite releases produce a client-ready ZIP per platform:

- `Setuora-Lite-<version>-Linux.zip`
- `Setuora-Lite-<version>-Windows.zip`

The ZIPs contain a clearly named `Install Setuora Lite.run` or
`Install Setuora Lite.cmd` plus a short `START HERE.txt`. The underlying
self-contained `.run` and `.cmd` files are also emitted for technical use.

Each self-extracting installer contains the reviewed application, Docker
Compose deployment, Caddy configuration, deployment documentation, and
platform lifecycle launcher. Like Setuora Master, the Windows package uses a
small `setuora.ps1` launcher around `deploy.py`; it does not contain an
executable installer. Packages exclude `.env`, credentials, databases, backups,
exported certificates, and Docker volume state.

## Build packages

From a reviewed release checkout:

```bash
python scripts/build_client_packages.py --version 1.0.0
```

The installers, delivery ZIPs, and `Setuora-Lite-<version>-SHA256SUMS.txt` are written to
`dist/`. Build from a clean, tagged revision, verify the checksums before
delivery, and retain the exact package used at each franchise for rollback.

The repository root also contains `Linux — Setuora Lite.run` and
`Windows — Setuora Lite.cmd`. The Linux shortcut launches the newest matching
package in `dist/`. On a Windows source checkout, the Windows shortcut builds a
package first when `dist/` is empty, then launches it. These repository
shortcuts are not distributable installers; give clients the generated
`Setuora-Lite-<version>-Windows.zip` file instead.

## Client prerequisites

- a current x86-64 Linux release with apt/dnf/yum/zypper, or a Docker-supported
  x86-64 Windows 10/11 build with WSL 2 capability, virtualization, and at
  least 8 GB RAM;
- administrator/sudo approval and outbound Internet access;
- a reserved private LAN address and outbound Internet access;
- the franchise-specific Master URL, franchise code, Setuora node credential,
  and tagged Tailscale enrollment key.

The guided package asks before installing third-party prerequisites. On Linux
it uses Docker's official install script and the distribution package manager.
On Windows it installs Python with WinGet, downloads Docker Desktop directly
from Docker, verifies its Authenticode signature, enables/updates WSL 2, and
starts Docker. The operator must accept Docker's license terms; organizations
that require a paid Docker Desktop subscription must provide it. A first-time
WSL enablement can require one reboot, after which setup is registered to
resume at sign-in.

## Install

On Linux, extract the delivery ZIP and run:

```bash
chmod +x "Install Setuora Lite.run"
./"Install Setuora Lite.run"
```

On Windows, extract the delivery ZIP and double-click
`Install Setuora Lite.cmd`, then approve the administrator prompt.

The package extracts to a stable per-user directory:

- Linux:
  `${XDG_DATA_HOME:-$HOME/.local/share}/setuora/Setuora-Lite-linux`;
- Windows: `%LOCALAPPDATA%\Setuora\Setuora-Lite-windows`.

It then delegates setup to the same `deploy.py` workflow as a source checkout.
Setup validates the franchise identity and network boundary, securely prompts
for credentials, starts Lite/Caddy/Tailscale, enrolls the Master cursor, exports
the public Caddy CA, configures LAN-only host firewall access where supported,
trusts the generated CA on the server, and prints/opens the LAN HTTPS URL.

## Update

Before updating, export and retain a verified backup and keep the current
installer. Run the newer package exactly as for initial setup. It detects the
existing `.env`, stops the deployment, replaces allowlisted application files,
runs preflight checks, rebuilds the containers, and verifies LAN health.

The fixed Compose project name preserves:

- `setuora-lite_setuora-data`
- `setuora-lite_tailscale-state`
- `setuora-lite_caddy-data`
- `setuora-lite_caddy-config`

Never distribute files copied from an active installation directory. Build
with the allowlisted package builder so secrets and state cannot enter the
installer. Never run `docker compose down --volumes` during normal operation.
