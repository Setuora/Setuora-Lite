# Setuora Lite Windows Package

The `.cmd` file is a self-extracting Windows installer and updater for the
franchise server. Run it as Administrator. It installs under
`C:\ProgramData\Setuora\Setuora-Lite-windows`.

Setup installs Python 3.11 with WinGet when necessary, creates an isolated
virtual environment, registers a Windows startup task, opens the application
port on the Private firewall profile, starts Lite, and verifies health. Docker,
WSL, and a private-network client are not installed.

Before enabling synchronization, obtain the franchise code, isolated SFTP
username/password, Master public address/port, and verified SHA256 SSH host-key
fingerprint. Enter these from **Admin → Tally SFTP**, together with the local
Tally company, host, and port.

Routine administration:

```powershell
$setuora = "C:\ProgramData\Setuora\Setuora-Lite-windows\setuora.ps1"
& $setuora status
& $setuora logs --follow
& $setuora stop
& $setuora start
```

Run a newer `.cmd` installer to update. The installer preserves `.env`, the
database, backups, and SFTP connection state.
