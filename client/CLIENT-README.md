# Setuora Lite Client Package

The Linux and Windows Setuora Lite installers are each delivered as one
self-extracting file. The same file handles a new installation and an update:

- when Setuora Lite is not installed, it extracts, configures, starts, enrolls,
  and verifies the complete application;
- when Setuora Lite is already installed, it stops the application, replaces
  release files, runs preflight checks, updates, restarts, and verifies it.

## Before installation

The franchise server needs:

- a current 64-bit Linux release using apt/dnf/yum/zypper, or a supported
  64-bit Windows 10/11 computer with at least 8 GB RAM and virtualization;
- administrator/sudo approval and outbound Internet access;
- a reserved private LAN IPv4 address and outbound Internet access;
- a one-off Tailscale auth key tagged `tag:setuora-lite`;
- the private Master `https://*.ts.net` URL;
- the franchise code and its one-time `setuora-node.*` credential;
- a unique first administrator password of at least 12 characters.

Use a different Tailscale key and Setuora node credential for every franchise.
The installer downloads and installs missing Docker/Python prerequisites after
the operator confirms the applicable third-party license. Tailscale runs as an
isolated Docker service and is enrolled automatically from the supplied key.

## Linux

Extract `Setuora-Lite-<version>-Linux.zip`, then run
`Install Setuora Lite.run`. If the desktop does not offer **Run in Terminal**,
open a terminal in that folder and run `chmod +x "Install Setuora Lite.run" &&
./"Install Setuora Lite.run"`.

Setuora Lite is installed under
`${XDG_DATA_HOME:-$HOME/.local/share}/setuora/Setuora-Lite-linux`. For daily
administration:

```bash
~/.local/share/setuora/Setuora-Lite-linux/setuora status
~/.local/share/setuora/Setuora-Lite-linux/setuora verify-sync
~/.local/share/setuora/Setuora-Lite-linux/setuora logs
```

## Windows

Extract `Setuora-Lite-<version>-Windows.zip` and double-click
`Install Setuora Lite.cmd`. Approve the Windows administrator prompt. The
installer adds Python, WSL 2, and Docker Desktop when missing; a newly enabled
WSL installation can require one automatic-resume reboot. Setuora Lite is installed under
`%LOCALAPPDATA%\Setuora\Setuora-Lite-windows`.

Daily commands can be run from PowerShell:

```powershell
& "$env:LOCALAPPDATA\Setuora\Setuora-Lite-windows\setuora.ps1" status
& "$env:LOCALAPPDATA\Setuora\Setuora-Lite-windows\setuora.ps1" verify-sync
& "$env:LOCALAPPDATA\Setuora\Setuora-Lite-windows\setuora.ps1" logs
```

## Install an update

1. Export and retain a verified database backup.
2. Keep the currently installed package for rollback.
3. Run the newer `.run` or `.cmd` installer.
4. Verify LAN HTTPS, Master connection, and pending event status.

The updater preserves `.env`, application data, backups, the Caddy CA, and the
Tailscale identity in Docker volumes. Never run
`docker compose down --volumes`; that command deletes persistent state.

For full instructions, see `docs/deployment/client-packages.md`.
