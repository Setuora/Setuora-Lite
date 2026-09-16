# Setuora Lite Windows Package

The `.cmd` file is a self-extracting Windows installer and updater for the
franchise server. Run it as Administrator. It installs under
`C:\ProgramData\Setuora\Setuora-Lite-windows`.

Setup installs Python 3.11 with WinGet when necessary, creates an isolated
virtual environment, registers a Windows startup task, opens the application
port on the Private firewall profile, starts Lite, and verifies health. Docker,
WSL, and a private-network client are not installed.

On Master, open **Franchises**, save its public HTTPS address once, and add
this franchise with its permanent code and Tally godown. Master automatically
creates the first credential. Select **Copy connection details** and transfer
the copied JSON securely to this Lite administrator.

On Lite, open **Admin → Master connection** (`/master-connection`), paste the
details, and select **Connect to Master**. Lite verifies the connection,
initializes inventory once, and starts synchronization. Confirm the baseline
is accepted on Master before staff begin work. Tally runs only at Master.
Master's public DNS, certificate, and HTTPS reverse proxy must already work;
the installers and connection form do not configure them.

Double-click
`C:\ProgramData\Setuora\Setuora-Lite-windows\setuora.bat` for the controls
menu: browser, start/stop, status, setup/repair, update, logs, and configuration
checks. Closing the menu leaves Lite running. Actions requiring Administrator
access open a visible console after Windows approval.

PowerShell commands are also available:

```powershell
$setuora = "C:\ProgramData\Setuora\Setuora-Lite-windows\setuora.ps1"
& $setuora status
& $setuora logs --follow
& $setuora stop
& $setuora start
```

Choose **Install downloaded update** and select a newer Lite `.cmd`
installer, or run that installer directly. The installer preserves `.env`,
the database, backups, and Master connection state.

See the [installation guide](docs/deployment/installation-guide.md).
