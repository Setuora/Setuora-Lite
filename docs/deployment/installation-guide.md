# Windows Installation Guide

1. Prepare a Windows Server 2019+ or current Windows 10/11 Pro franchise server.
2. Give it a stable private-LAN address and keep Tally reachable locally.
3. Build or obtain `Setuora-Lite-<version>-windows.cmd`.
4. Run the installer as Administrator. It installs under
   `C:\ProgramData\Setuora\Setuora-Lite-windows` and can install Python 3.11
   through WinGet.
5. Open `http://<server-name>:8000` from the private LAN, create the first
   administrator session, and configure Tally.
6. In **Admin → Tally SFTP**, enter the franchise code, Master public
   host/port, isolated SFTP username/password, and verified SHA256 host-key
   fingerprint.
7. Enable synchronization and run **Sync now**.

The installer registers a `Setuora-Lite` Windows startup task and a TCP 8000
inbound rule limited to the Windows Private firewall profile. The franchise
needs outbound SFTP access only; do not forward the Lite or Tally ports from the
Internet.

Use `setuora.ps1 status`, `logs --follow`, `stop`, and `start` for operations.
Run a newer installer for updates; `.env`, `data`, and backups are preserved.
