# Setuora Lite Client Package

The Linux and Windows Setuora Lite installers are each delivered as one
self-extracting file. The same file handles a new installation and an update:

- when Setuora Lite is not installed, it extracts, configures, starts, enrolls,
  and verifies the complete application;
- when Setuora Lite is already installed, it stops the application, replaces
  release files, runs preflight checks, updates, restarts, and verifies it.

## Before installation

The franchise server needs:

- a 64-bit Linux host with Docker Engine and Compose v2, or Windows 10/11 with
  Docker Desktop configured to use Linux containers;
- Python 3.11 or newer;
- a reserved private LAN IPv4 address and outbound Internet access;
- a one-off Tailscale auth key tagged `tag:setuora-lite`;
- the private Master `https://*.ts.net` URL;
- the franchise code and its one-time `setuora-node.*` credential;
- a unique first administrator password of at least 12 characters.

Use a different Tailscale key and Setuora node credential for every franchise.

## Linux

```bash
chmod +x Setuora-Lite-<version>-linux.run
./Setuora-Lite-<version>-linux.run
```

Setuora Lite is installed under
`${XDG_DATA_HOME:-$HOME/.local/share}/setuora/Setuora-Lite-linux`. For daily
administration:

```bash
~/.local/share/setuora/Setuora-Lite-linux/setuora status
~/.local/share/setuora/Setuora-Lite-linux/setuora verify-sync
~/.local/share/setuora/Setuora-Lite-linux/setuora logs
```

## Windows

Start Docker Desktop and double-click
`Setuora-Lite-<version>-windows.cmd`. Windows may ask you to confirm that you
trust the local script. Setuora Lite is installed under
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
