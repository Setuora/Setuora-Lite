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

New franchise QR serials are allocated in Setuora Master. Lite receives their product and serial data through the authenticated command poll and stores them locally for scanning and label printing. If Lite is offline, the records arrive when it reconnects. Create new QR labels in Master; Lite cannot allocate new serials.

Lite's **QR Inventory** page lists recently received codes. Existing labels can be printed again from Lite. Request a replacement in Master; the replacement reaches Lite as a command and its completed change returns to Master through the event outbox. During initial connection, Lite may enroll stock that existed before this Master-issued QR workflow.

For an existing installation, deliver all pending events for previously created Lite QR labels before upgrading Master and Lite. The new Master rejects later first-use events for serials it has not issued, except during the node's first historical inventory enrollment.

The former SFTP debtor/creditor exchange assumed Tally at each franchise. Its implementation remains in the repository for migration reference but is not started by the Lite application.

## Setup

1. Double-click `install-lite.bat` on a dedicated Windows 10/11 x64 franchise computer and approve the Administrator prompt. It downloads the latest Lite code, installs Git, Python, Caddy, and Tailscale as needed, configures startup tasks, and shows Lite's private HTTPS address. Sign in to the same Tailscale network as Master when prompted.
2. Join each staff PC to that tailnet and open Lite's `https://<lite-name>.<tailnet>.ts.net` address. Browser camera scanning requires this HTTPS address.
3. On Master, open **Franchises** (`/franchises`), save Master's private HTTPS address, and add a unique permanent franchise code. Copy that franchise's connection details securely to Lite.
4. In Lite, open **Admin → Master connection** (`/master-connection`), paste the copied JSON, and select **Connect to Master**. Verify the inventory baseline reached Master before staff begin work. Tally runs only at Master.

The Lite app and Caddy listen on localhost. Tailscale Serve provides private HTTPS to authorized tailnet PCs; do not forward Lite or Tally ports from the franchise network.

## Windows installation

The preferred handoff is the single `install-lite.bat` file. Running it again fetches a fast-forward update, creates a verified SQLite backup, and repairs the runtime without removing `.env`, the database, backups, or connection settings. The source checkout lives in `C:\ProgramData\Setuora\Setuora-Lite`.

For a fixed-version release handoff, build the self-extracting Windows installer:

```powershell
py -3.11 scripts\build_client_packages.py --version 1.0.0
```

Run `dist\Setuora-Lite-1.0.0-windows.cmd` as Administrator. It installs under `C:\ProgramData\Setuora\Setuora-Lite-windows` and preserves local data on update. Do not mix the Git and fixed-version installations on one computer.

Double-click `setuora.bat` in either a source checkout or an installed copy to open the same controls menu. Closing it leaves Setuora running. Setup, start, stop, update, and configuration checks (`preflight`) request Windows Administrator approval and show an interactive console for prompts and errors.

The installed update action asks for a downloaded Lite Windows `.cmd` installer. The Git installation updates through Git and requires a clean worktree and a fast-forward update. Native Windows controls still need acceptance testing on the actual deployment machines.

## Development

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.lock
copy .env.example .env
python -m pytest -q
```

See [central Tally topology](docs/architecture/central-tally-topology.md) and [installation guide](docs/deployment/installation-guide.md). The SFTP/Tally documents describe the previous franchise-Tally deployment and are not instructions for the central-Tally system.
