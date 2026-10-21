# Setuora Lite Windows package

The preferred client handoff is the Setuora-Lite repository: double-click its `setuora.bat` and choose **Start**. It downloads the latest Git version and installs under `C:\ProgramData\Setuora\Setuora-Lite`. This self-extracting `.cmd` package is a separate fixed-version release path under `C:\ProgramData\Setuora\Setuora-Lite-windows`. Do not use both on the same computer.

Run the `.cmd` package as Administrator on an x64 Windows 10/11 computer. Setup installs Python, the hash-locked app runtime, Caddy, and Tailscale as needed. It creates Lite and Caddy startup tasks, a local SQLite database, automatic local backups, and a private Tailscale Serve HTTPS route. It removes the old Lite LAN firewall rule. Follow the Tailscale sign-in link and any tailnet device approval step. The application and Caddy listen only on localhost and choose available ports automatically, including when Master is on the same computer.

Join each staff PC to the same tailnet and use the `https://<lite-name>.<tailnet>.ts.net` address printed by setup. This is the address for browser camera scanning. Secure cookies prevent using the internal HTTP health-check URL as a staff login.

On Master, create the franchise and copy its connection details. In Lite, open **Admin → Master connection**, paste those details, and select **Connect to Master**. Check that the initial inventory baseline reached Master. Tally runs only at Master.

Double-click `setuora.bat` in the installed folder for **Start**, **Stop** and **Uninstall**. Start repairs and runs Lite and opens its private HTTPS address; Stop keeps Lite stopped, even after a restart, until Start; Uninstall keeps a final recovery backup. A newer `.cmd` package preserves `.env`, the SQLite database, backups, and connection settings. Automatic backups remain on this computer until an off-machine destination is configured in the app.

See the [installation guide](docs/deployment/installation-guide.md).
