# Lite Windows installation

1. Prepare a Windows Server 2019+ or current Windows 10/11 Pro server in the franchise private LAN. Tally is not installed or configured here.
2. Obtain and run the `Setuora-Lite-<version>-windows.cmd` installer as Administrator. It creates a startup task and a Private-profile firewall rule for the Lite web application.
3. On Master, open **Franchises** (`/franchises`) and save its public HTTPS address once. Add this franchise's permanent code and Tally godown. Master creates the first credential automatically. Select **Copy connection details** and transfer the copied JSON securely to this Lite administrator. Each independent Lite needs a different code and credential.
4. From Lite's **Admin → Master connection** (`/master-connection`), paste those setup details and select **Connect to Master**. Lite checks the server, credential, and franchise identity before saving the connection.
5. The same action initializes inventory once and starts synchronization. Confirm the local baseline is accepted on Master before staff begin stock work. If the connection needs a replacement credential later, reconnect this same installation with Master's replacement details; do not reset its database or initialize a second franchise.
6. Test a supported operation and verify both its delivery on Lite and any resulting Tally voucher status on Master.

Install one Lite server for each independent warehouse/franchise. Staff PCs open
that server in a browser with separate user accounts; they do not need separate
Lite databases. Set a stable LAN address and include the hostname/IP used by
staff in `TRUSTED_HOSTS`. Test access from a staff PC. The packaged firewall rule
uses the Private profile; domain-managed networks need an appropriate scoped
Domain rule from their administrator. Master and Lite both use port 8000, so
their packaged services require separate machines/VMs.

Upgrade Master before Lite: the updated Lite includes sales discounts in its
events, which older Master builds reject. Pausing delivery retains new events;
initialize the permanent franchise identity before recording stock changes.

Before live use, verify reboot without login, real scanners/label printers,
concurrent scanning, offline work followed by restart/reconnection, partial
transfers, and backup restoration on the actual Windows PCs. Check vouchers in
the central Tally test company. Run `setuora.ps1 preflight`, `status`, and `logs`
from an Administrator PowerShell terminal in the installed folder. Installers
need dependency downloads and do not provide complete automatic upgrade rollback.

## Windows controls and updates

Double-click `setuora.bat` in the source checkout or installed folder to open the same controls menu. It provides browser access, start/stop, status, setup/repair, updates, logs, and configuration checks. Closing the menu leaves Setuora running.

Setup, start, stop, update, and `preflight` request Administrator approval through Windows UAC. Password prompts and errors appear in a visible Administrator console. Cancelling the approval leaves that action incomplete.

In an installed copy, choose **Install downloaded update** and select the downloaded `Setuora-Lite-<version>-windows.cmd` installer. In a source checkout, **Update from Git** requires a clean worktree and allows only a fast-forward update from its origin branch; it does not discard local changes. Back up the database and configuration first.

Native BAT/PowerShell, UAC, startup-task, and firewall behavior still need verification on the actual Windows machines; application tests alone do not validate these operating-system functions.

The franchise requires outbound HTTPS to Master. Keep Lite's web port within the private LAN. No franchise Tally or SFTP port is required. If the network is unavailable, event delivery waits in Lite's durable outbox.

Public DNS, a valid HTTPS certificate, and the Master reverse proxy are separate deployment requirements. Saving the address or copying the setup details does not create them; the Master installer itself binds the application to loopback. Keep Tally only at Master. The former `Admin → Tally SFTP` setup is obsolete for this central-Tally deployment.
