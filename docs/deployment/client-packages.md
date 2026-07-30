# Shareable Linux and Windows Client Packages

Setuora Lite releases can be delivered as one self-contained file per platform:

- `Setuora-Lite-<version>-linux.run`
- `Setuora-Lite-<version>-windows.cmd`

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

The installers and `Setuora-Lite-<version>-SHA256SUMS.txt` are written to
`dist/`. Build from a clean, tagged revision, verify the checksums before
delivery, and retain the exact package used at each franchise for rollback.

The repository root also contains `Linux — Setuora Lite.run` and
`Windows — Setuora Lite.cmd`. These local shortcuts launch the newest matching
package in `dist/`.

## Client prerequisites

- Docker Engine with Compose v2 on Linux, or Docker Desktop using Linux
  containers on Windows;
- Python 3.11 or newer;
- a reserved private LAN address and outbound Internet access;
- the franchise-specific Master URL, franchise code, Setuora node credential,
  and tagged Tailscale enrollment key.

The package does not silently install or elevate third-party software. Docker
installation can require a reboot and organization-specific licensing,
hardening, firewall, and startup configuration.

## Install

On Linux:

```bash
chmod +x Setuora-Lite-<version>-linux.run
./Setuora-Lite-<version>-linux.run
```

On Windows, start Docker Desktop and double-click
`Setuora-Lite-<version>-windows.cmd`.

The package extracts to a stable per-user directory:

- Linux:
  `${XDG_DATA_HOME:-$HOME/.local/share}/setuora/Setuora-Lite-linux`;
- Windows: `%LOCALAPPDATA%\Setuora\Setuora-Lite-windows`.

It then delegates setup to the same `deploy.py` workflow as a source checkout.
Setup validates the franchise identity and network boundary, securely prompts
for credentials, starts Lite/Caddy/Tailscale, enrolls the Master cursor, exports
the public Caddy CA, and prints the LAN HTTPS URL.

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
