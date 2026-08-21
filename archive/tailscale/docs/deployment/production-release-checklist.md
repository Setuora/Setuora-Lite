# Franchise Pilot Release Checklist

Run on the target franchise server before ordinary transactions.

## Build and Configuration

```bash
python deploy.py preflight
docker compose config --quiet
python deploy.py status
```

- [ ] Reviewed release source/checksums are used.
- [ ] Client installers were built with `scripts/build_client_packages.py`
      from a clean, tagged checkout and their SHA-256 checksums were retained.
- [ ] Docker starts automatically after host reboot.
- [ ] Lite runs exactly one Uvicorn process.
- [ ] `.env` is excluded from Git/build context and readable only by the
      deployment administrators where the platform supports it.
- [ ] Application, Tailscale, and Caddy volumes are persistent.
- [ ] Automatic verified backups are enabled with retention of at least two.

## LAN Boundary

- [ ] Caddy binds to the reserved private LAN interface only.
- [ ] Host/router firewall permits TCP 80/443 only from approved local subnets.
- [ ] No public router port-forward or cloud firewall exposes Lite.
- [ ] Approved staff devices trust the exported Caddy public root.
- [ ] LAN `/health` returns `{"status":"ok","role":"lite"}`.
- [ ] Login, CSRF protection, secure cookies, HSTS, role access, camera scan,
      label PDF, and report exports pass through the real LAN HTTPS URL.
- [ ] Uvicorn 8000, SQLite, and the Docker control socket are not remotely
      reachable.

## Tailscale and Master Identity

```bash
python deploy.py verify-sync
```

- [ ] This host has its own persistent `tag:setuora-lite` identity.
- [ ] The one-off Tailscale auth key was removed from `.env` after enrollment.
- [ ] `MASTER_URL` is Master's exact certificate-verified private
      `https://*.ts.net` origin.
- [ ] The tailnet grant permits Lite to Master TCP 443 only.
- [ ] There is no Lite Serve, Funnel, subnet route, Lite-to-Lite grant, or
      host-published proxy port.
- [ ] The Setuora node credential is unique, active, and the authenticated
      Master franchise code exactly matches `FRANCHISE_CODE`.
- [ ] The deployment enrollment marker is bound to Master's exact node
      `public_id`.
- [ ] Master shows the node online; `last_sequence` matches Lite's last `SENT`
      sequence and `next_sequence` is exactly one greater.

## Initial Data and Ordering

- [ ] A greenfield node has an explicitly accepted empty initialization event,
      or an existing node has an approved active `GENERATED`/`IN_STOCK`
      baseline.
- [ ] The initialization marker is sequence 1 and belongs to the permanent
      franchise code.
- [ ] Existing QR values were checked for global collision.
- [ ] Every initial snapshot chunk is within both 5,000 items and the 5 MiB
      frozen UTF-8 request-body limit.
- [ ] No ordinary transaction/outbox event predates initialization.
- [ ] Sold, issued, invalid, damaged, and pre-cutover report history has an
      explicit migrate/exclude decision.
- [ ] Every legacy `PENDING_SYNC`, `SYNCING`, or `FAILED` batch has an explicit
      settle, migrate, or exclude decision; the inventory baseline is not
      treated as historical transaction/Tally replay.

## Failure Acceptance

- [ ] With Master stopped, a local eligible workflow commits and the outbox
      grows.
- [ ] With Tailscale stopped, an offline stack restart still restores LAN HTTPS.
- [ ] Restoring connectivity replays events oldest-first without mutation.
- [ ] Lost-response retry produces one Master effect.
- [ ] Kill/restart during upload retries the frozen event.
- [ ] Revoked node credential blocks sync without losing local data; rotated
      credential resumes it.
- [ ] Command redelivery and lost acknowledgement are idempotent.
- [ ] Two Lite nodes cannot connect directly to one another.
- [ ] Partial/full transfer receipt and restart/outage locking pass.

## Tally Boundary

- [ ] Lite exposes no Tally route, setting, worker, or port 9000.
- [ ] Master event acceptance is not presented as Tally success.
- [ ] Tally configuration and attempts are visible only in Master.
- [ ] Master-side franchise company/Godown mapping and real-company validation
      are accepted before enabling production Tally posting.

## Backup and Recovery

- [ ] A fresh verified database backup was downloaded and copied off-machine.
- [ ] `.env` and the Caddy CA state have protected recovery copies.
- [ ] Tailscale state will not be cloned to another active franchise host.
- [ ] The team has rehearsed connected restore/cursor reconciliation on a clean
      host.
- [ ] Operators know never to run `docker compose down --volumes`.

This checklist approves only the bounded one-process SQLite pilot. PostgreSQL,
durable worker leases, centralized metrics/alerts, automated restored-cursor
reconciliation, and multi-node load/failover remain production-scale gates.
