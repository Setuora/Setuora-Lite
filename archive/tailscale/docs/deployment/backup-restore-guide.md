# Backup and Restore Guide

> **Connected Lite recovery is coordinated recovery.** Backup creation and
> download remain supported, but browser reset and restore are disabled while
> Master synchronization is enabled. Restoring an older database can make
> Lite's outbox cursor older than Master's accepted cursor. Do not reopen
> franchise operations until an administrator has compared
> `GET /api/v1/node`, reconciled inventory/ownership, and approved the next
> sequence.

## What Is Persistent

The supported Compose deployment keeps four independent state sets:

| State | Default location | Contains |
| --- | --- | --- |
| Application data | `setuora-lite_setuora-data` volume | SQLite database, schema safety copies, automatic backups, backup schedule/retention overrides, frontend-managed Master connection overrides |
| Lite configuration | `.env` beside `compose.yaml` | Application secret, initial Master connection defaults, deployment settings |
| Tailscale identity | `setuora-lite_tailscale-state` volume | This franchise server's private tailnet device identity |
| LAN certificate authority | `setuora-lite_caddy-data` volume | Caddy private CA and issued LAN certificates |

The SQLite backup does not contain `.env`, the frontend-managed
`master-connection.env`, the Tailscale identity, or Caddy's private CA. Protect
those separately. Never copy a Tailscale state volume to a different
franchise.

## Database Backup

1. Log in as an administrator.
2. Open `Maintenance`.
3. Click `Download backup`.
4. Store the downloaded `.db` file in encrypted off-machine storage.

The backup uses SQLite's online backup API, includes committed WAL data, and is
opened for integrity and foreign-key checks before it is offered.

Automatic verified backups run every 24 hours by default and retain the newest
14 copies inside the application data volume:

```dotenv
AUTOMATIC_BACKUPS_ENABLED=true
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_COUNT=14
BACKUP_STARTUP_DELAY_SECONDS=60
```

Inspect the in-volume copies without publishing the database:

```bash
docker compose exec setuora ls -la /srv/setuora/data/backups
```

The Compose deployment deliberately fixes `BACKUP_DIRECTORY` to that persistent
volume. Backup schedule/retention changes from Maintenance are stored in
`/srv/setuora/data/backup-settings.env`, not in the read-only application image.
Use reviewed host/volume backup software to copy verified backups off-machine;
the container UI deliberately rejects arbitrary paths that are not mounted.

Do not copy a live `setuora.db` file by itself. SQLite may have active
`-wal`/`-shm` files, and a raw copy is not the verified application backup.

## Configuration and Certificate Backup

Keep an encrypted, access-controlled copy of `.env` and, when the Master
connection has been edited in the frontend, the application volume's protected
`master-connection.env`. One of these contains the active long-lived Setuora
node credential. The one-off Tailscale enrollment key and bootstrap
administrator password are cleared after successful setup and should not be
restored.

Back up the Caddy data volume if staff devices must continue trusting the same
LAN CA after a host loss. The file exported by `python deploy.py export-ca` is
the public root and is safe to install on approved devices; the volume also
contains the private CA and must never be distributed.

Treat the Tailscale state volume as a private device identity. Prefer enrolling
a replacement device with a new one-off key during disaster recovery instead
of cloning that state to two active hosts.

## Connected Restore Procedure

1. Stop franchise operations and run `python deploy.py stop`.
2. Preserve the current application volume, `.env`, and an additional verified
   database backup before changing anything.
3. Revoke or isolate the node's Tailscale and Setuora credentials if compromise
   is suspected.
4. Restore the selected database while the application is stopped.
5. Start the stack without allowing ordinary operations.
6. Compare the restored outbox with Master's authenticated `last_sequence` and
   `next_sequence`.
7. Reconcile every in-transit transfer and current serial owner/status.
8. Rotate credentials when required, run `python deploy.py verify-sync`, and
   obtain an audited approval before reopening the LAN UI.

The current application does not automate steps 4–7. A database backup that
passes SQLite integrity checks can still be logically stale relative to Master.
Never delete/resequence outbox rows or mark them sent to force a match.

## Greenfield/Cutover Warning

An existing host-service database must be migrated deliberately and must queue
its active `GENERATED`/`IN_STOCK` inventory baseline before any ordinary Node
Sync event. The deployment helper refuses to silently ignore a legacy local
database or create a replacement volume in its place. Keep the source database
and its verified backup until Master inventory and cursor acceptance is signed
off. Before import, explicitly settle, migrate, or exclude every legacy
`PENDING_SYNC`, `SYNCING`, and `FAILED` batch; the current-inventory baseline
does not reconstruct those historical transaction or Tally effects.

Never use:

```text
docker compose down --volumes
```

during normal lifecycle or recovery preparation; it deletes the persistent
application, Tailscale, and Caddy volumes.
