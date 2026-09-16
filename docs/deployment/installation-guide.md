# Lite Windows installation

1. Prepare a Windows Server 2019+ or current Windows 10/11 Pro server in the franchise private LAN. Tally is not installed or configured here.
2. Obtain and run the `Setuora-Lite-<version>-windows.cmd` installer as Administrator. It creates a startup task and a Private-profile firewall rule for the Lite web application.
3. On Master, enroll this franchise's permanent code and issue a node credential. Each Lite must have a different code and credential.
4. From Lite's **Admin → Master connection**, enter that code, the exact `https://` origin of the Master node API, and the credential. Enable background synchronization.
5. Select **Initialize inventory** once. It verifies enrollment and queues the local baseline. Select **Sync now** and confirm the baseline is accepted on Master.
6. Test a supported operation and verify both its delivery on Lite and any resulting Tally voucher status on Master.

The franchise requires outbound HTTPS to Master. Keep Lite's web port within the private LAN. No franchise Tally or SFTP port is required. If the network is unavailable, event delivery waits in Lite's durable outbox.

The Master HTTPS reverse proxy is a separate deployment requirement; the Master installer itself binds the application to loopback. The former `Admin → Tally SFTP` setup is obsolete for this central-Tally deployment.
