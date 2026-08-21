# Backup and Restore Guide

Protect these together:

- `C:\ProgramData\Setuora\Setuora-Lite-windows\.env`;
- the `data` directory, including `setuora.db`, verified backups,
  `backup-settings.env`, and `sftp-connection.env`;
- the matching franchise SFTP account details and pinned host-key fingerprint.

Before restoring, stop Setuora Lite and confirm with the Master administrator
whether `/inbox`, `/outbox`, or `/ack` contains a pending exchange. Restore the
database and SFTP connection state from the same recovery point, start Lite,
and complete any waiting Master XML before enabling another upload.

Never acknowledge an outbound XML merely to clear the queue. An `.ack` means
that the matching file was imported successfully into the intended Tally
company.
