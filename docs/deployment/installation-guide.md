# Lite installation on Windows 10/11

Give the client **one file: `install-lite.bat`**. They double-click it on the dedicated franchise computer, approve the Windows Administrator prompt, choose a strong first administrator password, and follow the Tailscale sign-in link. If the tailnet requires device approval, its administrator approves this computer and the client runs the BAT again. Keep the window open until it shows the private HTTPS address. Internet access is needed for GitHub, Python dependencies, Caddy, and Tailscale downloads.

The BAT installs Git and Python when missing, clones the current `main` branch into `C:\ProgramData\Setuora\Setuora-Lite`, installs hash-locked Python packages, creates a persistent local SQLite database, configures automatic local backups, and registers startup tasks for Lite and Caddy. It signs Tailscale in with unattended mode and configures Tailscale Serve on private HTTPS port 443. Lite and Caddy listen only on localhost; no LAN firewall rule or router port forwarding is required. Do not install Lite and Master on the same computer because both use port 8000 internally.

Join every staff PC to the same tailnet and open the `https://<lite-name>.<tailnet>.ts.net` address shown by setup. Use that address for camera scanning. Staff still sign in with separate Lite user accounts. The Tailscale administrator controls which tailnet users can reach Lite. Lite's internal `http://127.0.0.1:8000` URL is for health checks and does not provide a staff browser login with secure cookies.

On Master, open **Franchises**, save Master's private HTTPS address, create the franchise's permanent code and Tally godown, and select **Copy connection details**. In Lite, open **Admin → Master connection**, paste those details, and select **Connect to Master**. Confirm the initial inventory baseline reached Master before staff record new stock activity. Tally runs only at Master. Each Lite installation needs its own code and credential.

## Updates and recovery

Double-click the same `install-lite.bat` again to fetch a fast-forward Git update and run Setup / repair. An update first creates and verifies an SQLite backup. Local `.env`, the database, backups, and Master connection settings are preserved. The update stops if the checkout has local changes or a different Git origin. Do not edit installed source files; use application settings and the controls menu. The BAT refuses to overwrite a separate packaged Lite installation. A source or database schema update cannot be rolled back automatically; retain the pre-update backup and previous source commit for recovery.

The controls menu is `C:\ProgramData\Setuora\Setuora-Lite\setuora.bat`. It opens the HTTPS address, checks status, starts or stops Lite, shows logs, and repairs setup. Status verifies the app, Caddy, the Serve route, and the tailnet HTTPS health endpoint. A restart of the Windows computer should bring both startup tasks and unattended Tailscale back without a user login.

Automatic backups are stored under `data\backups` on this computer. They protect against a damaged database, but a lost or failed computer can also lose those backups. Configure an off-machine backup destination later in Lite's backup settings when one is available. Do not delete the data folder during repair or update.

Before live use, test a reboot without login, a staff PC browser login, camera scanning, printers, offline work followed by reconnection, concurrent scanning, and a backup restoration on the actual Windows machines. Verify Tally vouchers in a test company. Native Windows startup and Tailscale sign-in behavior cannot be fully validated by the application unit tests.

Windows 10 production machines need active Extended Security Updates or a supported LTSC lifecycle; ordinary Windows 10 support ended on 14 October 2025. Windows 11 should be kept on a supported release. The installer needs x64 hardware for the current Python runtime.

The older `Setuora-Lite-<version>-windows.cmd` self-extracting release package is a separate installation path under `C:\ProgramData\Setuora\Setuora-Lite-windows`. It cannot replace or coexist with the Git installation on the same computer. Run a newer release package to update that path.
