# Setuora Lite

Setuora Lite is the local operations application at each franchise. It does not require Tally at the franchise. Each Lite installation has its own SQLite database and permanent franchise code.

## Current architecture

```text
Franchise A: Lite + local event queue ── HTTPS ──┐
Franchise B: Lite + local event queue ── HTTPS ──┼── Setuora Master
Franchise C: Lite + local event queue ── HTTPS ──┘       │
                                            central database and Tally queue
                                                        │
                                                   one central Tally
```

Lite commits each supported inventory, transfer, receipt, or batch event locally before its background worker sends events in sequence to Master. Master authenticates each franchise with a separate node credential, records events idempotently, and provides commands that Lite polls and acknowledges. Internet loss leaves events in Lite's durable outbox for later retry. Master acceptance of an event does **not** mean its Tally voucher has completed; check voucher status on Master.

The former SFTP debtor/creditor exchange assumed Tally at each franchise. Its implementation remains in the repository for migration reference but is not started by the Lite application.

## Setup

1. Install Lite on a Windows server in the franchise's private LAN. The installer creates a startup task and opens the Lite web port only on the Windows Private firewall profile.
2. Enroll a unique permanent franchise code on Master and issue that franchise's node credential.
3. Publish Master's `/api/v1` endpoints through a reviewed HTTPS reverse proxy. Keep the Master admin console and central Tally gateway private.
4. In Lite, open **Admin → Master connection**. Enter the same franchise code, the exact HTTPS origin, and the issued node credential. Enable background synchronization.
5. Select **Initialize inventory** once to queue the inventory baseline, then **Sync now**. Review event delivery on Lite and Tally voucher status on Master.

The Lite server makes outbound HTTPS requests only. Its private web port and the central Tally port should not be forwarded from the franchise network.

## Windows installation

Build the self-extracting Windows installer:

```powershell
py -3.11 scripts\build_client_packages.py --version 1.0.0
```

Run `dist\Setuora-Lite-1.0.0-windows.cmd` as Administrator on the franchise server. Production files are installed under `C:\ProgramData\Setuora\Setuora-Lite-windows`. A newer installer preserves `.env`, the local database, backups, and connection settings.

For a source checkout, use `setuora.bat setup`, `start`, `stop`, or `update`. For a packaged installation, use the installed `setuora.ps1` for status, logs, start, and stop.

## Development

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.lock
copy .env.example .env
python -m pytest -q
```

See [central Tally topology](docs/architecture/central-tally-topology.md) and [installation guide](docs/deployment/installation-guide.md). The SFTP/Tally documents describe the previous franchise-Tally deployment and are not instructions for the central-Tally system.
