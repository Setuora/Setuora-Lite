# Setuora Lite Windows Package

The `.cmd` file is a self-extracting Windows installer and updater for the
franchise server. Run it as Administrator. It installs under
`C:\ProgramData\Setuora\Setuora-Lite-windows`.

Setup installs Python 3.11 with WinGet when necessary, creates an isolated
virtual environment, registers a Windows startup task, opens the application
port on the Private firewall profile, starts Lite, and verifies health. Docker,
WSL, and a private-network client are not installed.

Before enabling synchronization, obtain the permanent franchise code, a unique
Master node credential, and the Master HTTPS origin. Enter them under
**Admin → Master connection**, initialize inventory once, and run the first
sync. Tally is configured and operated only on Master.

Routine administration:

```powershell
$setuora = "C:\ProgramData\Setuora\Setuora-Lite-windows\setuora.ps1"
& $setuora status
& $setuora logs --follow
& $setuora stop
& $setuora start
```

Run a newer `.cmd` installer to update. The installer preserves `.env`, the
database, backups, and Master connection state.
