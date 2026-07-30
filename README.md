# Setuora Lite — Franchise Operations Node

`Setuora-Lite` runs inside a franchise LAN and owns the operational workflows:
purchase, sale, return, issue, audit, QR generation/printing, and local inventory
history. It is designed to continue local capture during an Internet outage.

Lite connects to Setuora Master using outbound HTTPS only. It
uploads durable, idempotent events, polls Master for commands and incoming
transfers, and never requires an inbound WAN firewall rule. Master—not Lite—owns
consolidated monitoring and the central Tally queue.

> **Rollout status — self-hosted Lite/Master pilot implemented; operational
> acceptance pending.** Lite mode has an ordered durable outbox, idempotent
> command inbox, outbound sync worker, franchise-namespaced QR generation, and
> partial/full inter-franchise transfers. The supported deployment adds
> LAN-only Caddy HTTPS and an outbound-only Tailscale identity. Direct Tally
> routes and the Tally retry worker are excluded from Lite. Existing-data
> cutover, cursor recovery, credential drills, and two-node outage/restore/soak
> tests remain rollout gates.

See [Lite Node Synchronization](docs/architecture/lite-node-sync.md) and the
normative
[Node Sync API v1](../Setuora-Master/docs/api/node-sync-v1.md).

## Current Local Foundation

- Role-based login for admin, purchase, sales, and audit users
- Product master with HSN, GST, unit, default rate, sales discount, and exact Tally stock item name
- Bulk serial generation and printable/PDF QR labels with the serial number only
- Product batch, manufacturing date, expiry date, and warehouse tracking for assigned stock
- Purchase, sale, audit, sales return, purchase return, stock issue, barcode assignment, and barcode replacement workflows
- Atomic local inventory plus durable Master-outbox commit for submitted batches
- Strict oldest-first event replay with immutable UUID, sequence, payload, and SHA-256
- Fail-closed 5,000-item/5 MiB frozen-body limits with byte-aware initial
  baseline chunking
- Durable idempotent Master-command inbox and outbound command polling/ACK
- Franchise-code namespacing for newly generated QR values in Lite mode
- Source dispatch locking and destination manifest scan with partial/full receipt
- Batch pricing, GST split, round off, and voucher preview before submit
- FEFO picking and expiry control for sale, issue, and purchase-return batches
- Inherited direct Tally XML, readiness, discovery, profile, and retry paths;
  these require an explicit test-only legacy gate and are not registered in Lite
- Editable admin role access controls for pages, actions, and data areas
- Audit reconciliation for verified, missing, and extra serials
- Dashboard counts, charts, recent activity, and live refresh
- Configurable stock movement, stock-cover, slow/dead stock, overstock, and expiry-risk analysis with warehouse/franchise filters
- Excel reports, transaction history, scan history, and PDF audit reports
- SQLite-safe backup download and restore procedure
  (connected Lite nodes deliberately block browser reset/restore until cursor
  reconciliation exists)

The Master connection page provides a required one-time initialization event:
an active `GENERATED`/`IN_STOCK` snapshot for an existing-data site or an empty
heartbeat for a greenfield site. Ordinary events are fail-closed until that
marker exists for the permanent franchise code. Historical sold/issued
migration, destination directory/cancel recovery, automated cursor
reconciliation after restore, stronger sync-health alerts, durable worker
leases, and two-node failure/soak testing remain rollout work. The current
worker and SQLite database are a single-process MVP.

## Folder Structure

```text
Setuora-Lite/
|-- compose.yaml                      Lite, Caddy, and Tailscale services
|-- Dockerfile                        Non-root Lite runtime image
|-- deploy.py                         Linux/Windows deployment lifecycle
|-- requirements-runtime.lock         Hash-verified container dependencies
|-- client/                           Packaged Linux/Windows lifecycle launchers
|-- scripts/build_client_packages.py  Single-file installer builder
|-- app/                              FastAPI application
|   |-- main.py                       App entrypoint and route registration
|   |-- models.py                     SQLAlchemy database models
|   |-- routers/                      Page and API route handlers
|   |-- services/                     Business logic and integrations
|   |-- static/                       Browser JavaScript, CSS, and assets
|   `-- templates/                    Jinja HTML templates
|-- deployment/caddy/Caddyfile.container  LAN HTTPS reverse proxy
|-- docs/                             Architecture and operating guides
|-- tests/                            Pytest coverage
`-- data/                             Local-development data, ignored by git
```

## Supported Self-Hosted Deployment

The same deployment is supported on Linux and Windows:

- Docker Engine with Compose v2, or Docker Desktop using Linux containers;
- a reserved private LAN address or reviewed LAN DNS name;
- reliable UTC time and outbound Internet access for Tailscale;
- a one-off, non-ephemeral, pre-authorized Tailscale key tagged
  `tag:setuora-lite`;
- the private Master URL printed by Master setup;
- the one-time `setuora-node.*` credential issued for this exact franchise.

Tally is not a Lite prerequisite and port 9000 must not be reachable from this
stack.

First enroll the franchise in Master with its permanent code. Then, from the
Lite project root, run:

```bash
python deploy.py setup
```

The helper validates Docker, creates and protects `.env`, prompts without
putting secrets in shell history, starts Lite/Caddy/Tailscale, verifies the
database-backed health endpoint and LAN HTTPS, persists the Tailscale identity,
removes bootstrap secrets, and checks authenticated `GET /api/v1/node` through
the Tailscale proxy. Setup fails if the Master credential belongs to a different
franchise code.

The supported topology is:

```text
Staff browser
  -> franchise LAN HTTPS
  -> Caddy
  -> Setuora Lite + SQLite/outbox
  -> Docker-internal HTTP CONNECT proxy
  -> Tailscale (tag:setuora-lite)
  -> private Master *.ts.net:443
```

Lite has no Tailscale Serve/Funnel listener. Tailscale and Master may be offline
while Lite and Caddy start and serve LAN operations; synchronization retries
from the durable outbox when connectivity returns.

Daily commands:

```bash
python deploy.py status
python deploy.py preflight
python deploy.py verify-sync
python deploy.py logs
python deploy.py update
python deploy.py stop
python deploy.py start
python deploy.py export-ca
```

`stop` preserves all application, Tailscale, and Caddy volumes. Never use
`docker compose down --volumes` during normal operation.

Install the public CA file exported by `setup`/`export-ca` on each approved
staff phone or workstation before opening the printed LAN HTTPS URL. Keep the
Caddy data volume private; only the exported public root is distributed.

### Shareable Linux and Windows Installers

Build client-ready, self-contained installers from a reviewed release:

```bash
python scripts/build_client_packages.py --version 1.0.0
```

This creates a Linux `.run`, a double-clickable Windows `.cmd`, and SHA-256
checksums in `dist/`. Each package installs or updates the complete Docker
deployment without Git and excludes `.env`, credentials, databases, backups,
exported certificates, and Docker volume state. See the
[client package guide](docs/deployment/client-packages.md).

The repository root shortcuts `Linux — Setuora Lite.run` and
`Windows — Setuora Lite.cmd` launch the newest matching package in `dist/`.

Like Setuora Master, the Windows package uses a PowerShell lifecycle launcher
for the same `deploy.py` commands. It contains no executable installer and does
not maintain a second deployment implementation.

### Existing Local Database

Setup refuses to silently ignore a legacy `data/*.db`. Stop the old service,
create and retain a verified backup, and complete the reviewed active-inventory
baseline/collision procedure before switching to the named Docker volume.
Never allow the first ordinary event to precede the initialization marker.
Connected restores also require Master cursor reconciliation; see the
[backup and restore guide](docs/deployment/backup-restore-guide.md).

### Local Development

Python 3.11 development remains available:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
uvicorn app.main:app --reload
```

Use test-only legacy mode only for the inherited regression suite. Do not use
it on a franchise deployment.

### First Administration

1. Sign in with the administrator account created during setup.
2. Confirm `Master connection` shows the initialization event sent and no
   blocked oldest event.
3. In Master, confirm the franchise cursor and initial inventory/empty baseline.
4. Create product masters with complete HSN, GST, unit, rate, and exact Tally
   stock-item metadata for Master.
5. Create named users and assign only the required role access.
6. Complete the release checklist before ordinary transactions.

## 8. Normal Workflow

Purchase stock:

1. Open `Batches` -> `Purchase`.
2. Enter supplier/reference.
3. Scan serials.
4. Check the voucher preview.
5. Submit the batch.

Sell stock:

1. Open `Batches` -> `Sale`.
2. Enter customer/reference.
3. Scan in-stock serials.
4. Use `Pick FEFO` when selling by product and quantity, or scan the earliest-expiry serials manually.
5. Check pricing, GST, round off, and final value.
6. Submit the batch.

Audit stock:

1. Open `Batches` -> `Audit`.
2. Enter location/reference.
3. Scan physical stock.
4. Submit the audit.
5. Review verified, missing, and extra findings.

Returns and issue:

- `Sales return`: scan sold items returned by customer.
- `Purchase return`: scan or FEFO-pick in-stock items returned to supplier.
- `Issue`: scan or FEFO-pick in-stock items issued for sample, office use, damage, marketing, production, or other reasons.

QR label assignment:

1. Open `Barcodes` -> `Assignment`.
2. Select an existing product and quantity, or upload an Excel file.
3. Excel can use `Product Code` or `Product Name` with `Quantity`; optional columns include `HSN`, `GST`, `SGST`, `IGST`, `Batch`, `Mfg Date`, `Expiry Date`, and `Warehouse`. Tally invoice exports with `Description of Goods` and `Quantity` are also accepted.
4. Download the generated Excel file and labels PDF.

Barcode replacement:

1. Open `Barcodes` -> `Replacement`.
2. Enter the damaged/old serial.
3. Leave new serial blank to auto-generate, or enter a new serial manually.
4. Print the new label.

## 9. Tally Boundary

Lite never connects to Tally or opens port 9000. It sends complete, immutable
transaction events to Master; Master owns company/Godown mapping, the durable
Tally queue, retry, posting, and reconciliation. A Master event acknowledgement
is not Tally success.

The inherited direct-Tally composition is test-only migration code and cannot
be enabled by the supported Compose deployment. See
[Tally boundary](docs/deployment/tally-integration-guide.md).

## 10. Reports And Exports

Use `Reports` for:

- Scan history
- Transaction history
- Pending sync
- Excel export
- Expiry summary context

Use batch detail pages for:

- local transaction and item detail
- Master event state and retry context
- Audit PDF export

Use label pages for:

- Browser print
- QR label PDF download
- Serial XLSX download

Use `Expiry` for:

- Expiring stock bands
- Slow-moving expiry risk
- Sleeping stock
- Warehouse expiry exposure
- Shortcuts to product batch entry and FEFO sale

## 11. Backup And Restore

Backup:

1. Open `Maintenance`.
2. Click `Download backup`.
3. Store the downloaded `.db` file safely.
4. Keep a separate copy of `.env`.

Automatic verified backups run every 24 hours inside the persistent application
volume, retain the newest 14, and pass SQLite integrity/foreign-key checks.
Copy verified backups off-machine with reviewed volume/host backup tooling and
keep a separate encrypted copy of `.env`.

Browser import/reset is deliberately blocked on connected Lite. An older
database can have an outbox cursor behind Master even when SQLite is healthy.
Connected recovery must compare Master's cursor and reconcile inventory and
transfers before operations resume. See the
[backup and restore guide](docs/deployment/backup-restore-guide.md).

## 12. Run Tests

```bash
pytest
```

Or:

```bash
python -m pytest
```

Expected result:

```text
All collected tests pass.
```

The current pinned dependencies are verified with Python 3.11. A Python 3.13 virtual environment may fail before tests start with the current SQLAlchemy pin.

## 13. LAN Phone Camera Setup

Phone camera access requires HTTPS when opened from another LAN device. The
Compose Caddy service provides it automatically:

1. Reserve the configured `SETUORA_LAN_BIND_ADDRESS` on the franchise router or
   server.
2. Allow TCP 80/443 only from the approved private LAN.
3. Run `python deploy.py export-ca`.
4. Install the exported public root certificate on every approved phone and
   workstation.

Recommended production shape:

```text
Phone browser -> https://<franchise-LAN-host> -> Caddy -> Setuora container:8000
```

See [LAN HTTPS](docs/deployment/https-lan-guide.md). Back up the private
`setuora-lite_caddy-data` volume, but distribute only the exported public root
certificate.

## 14. Automatic Startup

Compose applies `restart: unless-stopped` to Lite, Caddy, and Tailscale. Configure
Docker Engine/Desktop to start at host boot. Lite/Caddy do not wait for
Tailscale health, so an offline reboot still brings the local UI back.

## 15. Useful Deployment Docs

- `docs/architecture/lite-node-sync.md`
- `docs/architecture/adr-002-lite-self-hosted-tailscale-egress.md`
- `../Setuora-Master/docs/api/node-sync-v1.md`
- `../Setuora-Master/docs/architecture/adr-001-master-lite-control-plane.md`
- `docs/deployment/installation-guide.md`
- `docs/deployment/client-packages.md`
- `docs/deployment/https-lan-guide.md`
- `docs/deployment/user-manual.md`
- `docs/deployment/backup-restore-guide.md`
- `docs/deployment/tally-integration-guide.md`

## Troubleshooting

If login does not work:

- Run `python deploy.py status` and `python deploy.py logs setuora`.
- Confirm the browser uses the printed HTTPS LAN URL.
- Check the bootstrap username/password.

If camera does not open on phone:

- Use Chrome or Edge.
- Serve the app over HTTPS on the LAN.
- Confirm the browser has camera permission.

If Master synchronization is blocked:

- Run `python deploy.py verify-sync`.
- Confirm Tailscale is online and the Master URL is the private `*.ts.net` URL.
- Confirm the node credential is active and belongs to this franchise code.
- Open `Master connection` and inspect the oldest failed event; later events
  intentionally remain blocked behind it.

If the app fails to start:

- Run `python deploy.py preflight`.
- Confirm Docker Engine and Compose v2 are running.
- Check that the configured LAN ports/address are available.
- Run `python deploy.py logs setuora` and `python deploy.py logs caddy`.
