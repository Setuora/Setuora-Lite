# Setuora Lite

> Offline-capable franchise operations node synchronized with Setuora Master.

## Overview

Setuora Lite runs inside a franchise LAN and owns local purchase, sale, return,
issue, audit, QR, transfer, and inventory-history workflows. It continues to
capture operations when Internet or Master connectivity is unavailable.

Lite connects to Setuora Master using outbound HTTPS only. It uploads durable,
idempotent events and polls for commands and incoming transfers; it does not
require an inbound WAN firewall rule or a direct Tally connection.

## Status

The self-hosted Lite/Master pilot is implemented and awaiting operational
acceptance. Remaining rollout gates include existing-data cutover, cursor and
credential recovery drills, stronger sync-health validation, and two-node
outage, restore, and soak testing.

The current worker and SQLite database are a single-process MVP. Connected
browser reset and restore remain blocked until cursor reconciliation is
available.

## Features

- Role-based local operations for administration, purchase, sales, and audit
- Product, batch, warehouse, manufacturing, expiry, and serial tracking
- Purchase, sale, audit, return, issue, barcode assignment, and replacement
- Bulk QR generation with franchise-code namespacing
- FEFO picking, expiry controls, GST, round-off, and voucher previews
- Atomic local inventory changes with a durable ordered Master outbox
- Immutable event UUIDs, sequence numbers, payloads, and SHA-256 hashes
- Idempotent Master command inbox with polling and acknowledgement
- Partial and full inter-franchise transfer dispatch and receipt
- Dashboard analysis, reports, Excel exports, and audit PDFs
- Verified automatic SQLite backups
- LAN-only Caddy HTTPS and outbound-only Tailscale identity

## Architecture

```text
Staff phone or workstation
  -> franchise LAN HTTPS
  -> Caddy
  -> Setuora Lite / SQLite / durable outbox
  -> Docker-internal HTTP CONNECT proxy
  -> Tailscale (tag:setuora-lite)
  -> private Setuora Master HTTPS endpoint
  -> central Tally queue and reconciliation
```

Lite has no Tailscale Serve or Funnel listener. Local services start without
Master or Tailscale availability, and the outbox resumes oldest-first delivery
when connectivity returns.

Lite never connects directly to Tally or opens port 9000. A Master event
acknowledgement confirms durable receipt by Master; it does not mean that Tally
posting succeeded.

## Repository Structure

```text
Setuora-Lite/
|-- app/                         FastAPI application
|   |-- routers/                Page and API route handlers
|   |-- services/               Business logic and synchronization
|   |-- static/                 Browser assets
|   `-- templates/              Jinja templates
|-- client/                     Linux and Windows lifecycle launchers
|-- deployment/caddy/           LAN HTTPS reverse-proxy configuration
|-- docs/                       Architecture and operating guides
|-- scripts/                    Client package builder and support scripts
|-- tests/                      Pytest coverage
|-- data/                       Local development data, ignored by Git
|-- compose.yaml                Lite, Caddy, and Tailscale services
|-- Dockerfile                  Non-root application image
|-- deploy.py                   Deployment lifecycle entry point
|-- requirements-runtime.lock   Hash-verified container dependency lock
`-- .env.example                Configuration template
```

## Requirements

- Docker Engine with Compose v2, or Docker Desktop with Linux containers
- A reserved private LAN address or reviewed local DNS name
- Reliable UTC time and outbound Internet access for Tailscale
- A one-off, non-ephemeral, pre-authorized key tagged `tag:setuora-lite`
- The private Master URL issued during Master setup
- A one-time `setuora-node.*` credential for the enrolled franchise code

Tally is not a Lite prerequisite and its port 9000 must not be reachable from
the Lite stack.

## Installation

Enroll the franchise in Master using its permanent code, then run from the Lite
repository root:

```bash
python deploy.py setup
```

Setup validates Docker, creates and protects `.env`, enrolls the persistent
Tailscale identity, starts Lite and Caddy, verifies health and LAN HTTPS, checks
the authenticated Master endpoint, and removes bootstrap secrets. Setup fails
if the node credential belongs to a different franchise.

For client-ready Linux and Windows packages:

```bash
python scripts/build_client_packages.py --version 1.0.0
```

Distribute the generated ZIP for the target platform from `dist/`, not a
repository shortcut. See the [client package guide](docs/deployment/client-packages.md).

## Configuration

Supported setup writes `.env` interactively. Important settings include:

- application secret, bootstrap administrator, database, and session controls
- `FRANCHISE_CODE`, which must be permanent and unique before label generation
- LAN bind address, hostname, and Caddy ports
- Tailscale enrollment key, hostname, and `tag:setuora-lite`
- private `MASTER_URL`, node API key, TLS verification, and sync intervals
- backup schedule, retention, and optional off-machine directory

Do not enter production secrets on a shared command line or commit `.env`.

An existing site must send a one-time initialization event before ordinary
events: either an active `GENERATED`/`IN_STOCK` snapshot or an empty greenfield
heartbeat. Never let an ordinary event precede this marker for the permanent
franchise code.

## Usage

First administration:

1. Sign in with the administrator account created during setup.
2. Confirm **Master connection** shows the initialization event and no blocked
   oldest event.
3. Confirm the franchise cursor and initial inventory in Master.
4. Create complete product masters and named least-privilege users.
5. Complete the release checklist before ordinary transactions.

Typical inventory flow:

1. Open **Batches** and choose Purchase, Sale, Audit, Return, or Issue.
2. Enter the counterparty, location, or reference.
3. Scan serials or use FEFO picking where available.
4. Review item state, pricing, GST, round-off, and voucher preview.
5. Submit and review both local state and Master event state.

Use **Barcodes > Assignment** to generate labels from product quantities or an
Excel upload. Use **Barcodes > Replacement** to retire a damaged serial and
print a replacement.

## Operations

Routine lifecycle commands:

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

`stop` preserves the application, Tailscale, and Caddy volumes. Never use
`docker compose down --volumes` during normal operation.

Install the exported public Caddy root certificate on each approved LAN device.
Keep the private Caddy data volume protected. Automatic verified backups run
every 24 hours and retain the newest 14 by default; copy reviewed backups and a
protected copy of `.env` off the host.

Restoring a connected node requires comparison with Master's cursor and
reconciliation of inventory and transfers. Follow the backup guide before
replacing the database.

## Development

Python 3.11 local development remains available:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
uvicorn app.main:app --reload
```

Legacy direct-Tally mode exists only for inherited regression coverage. Do not
enable it on a franchise deployment.

## Testing

Run the test suite from an activated Python 3.11 environment:

```bash
python -m pytest
```

All collected tests must pass before an update or release. Python 3.13 may fail
before test collection with the current SQLAlchemy pin.

## Security

- Allow LAN ports 80/443 only from approved private networks.
- Keep Lite free of inbound WAN, Tailscale Serve, and Funnel listeners.
- Keep Tally port 9000 unreachable from the Lite stack.
- Protect `.env`, node credentials, databases, backups, and Caddy private state.
- Preserve ordered delivery, idempotency, request-size limits, and the
  initialization gate when changing synchronization.
- Distribute only the exported public CA certificate, never Caddy private data.

## Documentation

- [Lite synchronization architecture](docs/architecture/lite-node-sync.md)
- [Self-hosted Tailscale egress ADR](docs/architecture/adr-002-lite-self-hosted-tailscale-egress.md)
- [Installation guide](docs/deployment/installation-guide.md)
- [Client packaging](docs/deployment/client-packages.md)
- [LAN HTTPS](docs/deployment/https-lan-guide.md)
- [User manual](docs/deployment/user-manual.md)
- [Backup and restore](docs/deployment/backup-restore-guide.md)
- [Tally boundary](docs/deployment/tally-integration-guide.md)
- [Production release checklist](docs/deployment/production-release-checklist.md)

## Troubleshooting

- **Login fails:** run `python deploy.py status`, inspect
  `python deploy.py logs setuora`, and use the printed HTTPS LAN URL.
- **Camera access fails:** use Chrome or Edge over LAN HTTPS, install the public
  CA certificate, and grant browser camera permission.
- **Master sync is blocked:** run `python deploy.py verify-sync`, confirm the
  private Master URL and node credential, then inspect the oldest failed event.
  Later events intentionally wait behind it.
- **The application does not start:** run `python deploy.py preflight`, confirm
  Docker and Compose v2 are running, and verify the configured address and ports.
- **Recovery is required:** do not reset or import through the browser; follow
  the connected-node reconciliation procedure in the backup guide.
