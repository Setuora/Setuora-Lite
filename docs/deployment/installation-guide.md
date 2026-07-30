# Self-Hosted Installation Guide

## Supported Topology

Each franchise runs one Docker Compose project:

```text
Approved LAN browsers
  -> private LAN TCP 443
  -> Caddy internal-CA HTTPS
  -> Setuora Lite (one process) -> persistent SQLite/outbox
                                      |
                                      v
                         Docker-internal HTTPS proxy
                                      |
                         Tailscale tag:setuora-lite
                                      |
                         Master private *.ts.net:443
```

Lite and Caddy remain available when Tailscale, the Internet, or Master is
offline. Lite does not use Tailscale Serve or Funnel and does not accept an
inbound WAN connection.

## Prerequisites

On the franchise server:

- Linux with Docker Engine/Compose v2, or Windows with Docker Desktop using
  Linux containers;
- Docker configured to start after host reboot;
- a reserved private LAN IPv4 address;
- TCP 80/443 allowed from the approved local subnet only;
- reliable system time and outbound Internet access.

Prepared centrally:

- Master is already deployed at a private HTTPS `*.ts.net` URL;
- the franchise has a permanent unique code in Master;
- Master has issued the one-time-visible `setuora-node.*` credential for that
  franchise;
- Tailscale has a one-off, pre-authorized, non-ephemeral auth key tagged
  `tag:setuora-lite`;
- the tailnet grant permits `tag:setuora-lite` to
  `tag:setuora-master` on TCP 443 only.

Use a different Tailscale enrollment key and Setuora node credential for every
franchise.

## First Setup

Open a terminal in the reviewed Lite release and run:

```bash
python deploy.py setup
```

The same command is used on Linux and Windows. The helper:

- validates Docker Engine and Compose;
- creates `.env` with restrictive permissions where supported;
- generates a random application secret;
- prompts securely for the first administrator password, Tailscale enrollment
  key, and Setuora node key;
- validates the permanent franchise code, private Master URL, LAN hostname, and
  private bind address;
- builds and starts Lite, Caddy, and Tailscale;
- verifies SQLite-backed application health and LAN HTTPS;
- waits for this installation's persistent Tailscale identity;
- removes the one-off Tailscale key and bootstrap password from `.env`;
- exports Caddy's public root certificate;
- calls authenticated `GET /api/v1/node` through the Tailscale proxy and
  refuses a credential/franchise-code mismatch;
- binds the resumable enrollment marker to Master's exact node `public_id`;
- initializes and verifies sequence 1 for a genuinely new empty node.

Secrets are not placed in shell history or printed in expanded Compose output.
Keep `.env` out of source control and in encrypted configuration backups.

Setup is intentionally strict. A regular `start` remains offline-tolerant, but
first enrollment is not complete until both Tailscale and Master identity checks
pass. Fresh enrollment requires Master's exact cursor to be `0/1`; completion
requires Master's accepted cursor to match Lite's last `SENT` sequence and next
sequence. Established nodes retain that identity binding across later
offline-tolerant restarts.

## LAN Certificate

Setup prints the LAN HTTPS URL and exported public CA path. Install that public
root certificate on every approved staff phone/workstation before opening the
site. Do not distribute the Caddy data volume; it contains the private CA.

Export the public root again when needed:

```bash
python deploy.py export-ca
```

See [LAN HTTPS](https-lan-guide.md).

## Daily Lifecycle

```bash
python deploy.py status
python deploy.py preflight
python deploy.py verify-sync
python deploy.py logs
python deploy.py logs setuora
python deploy.py logs caddy
python deploy.py logs tailscale
python deploy.py stop
python deploy.py start
python deploy.py update
```

`start` and `update` require the local application and LAN HTTPS to become
healthy. They report Tailscale/Master degradation separately and do not make a
WAN outage block local operation.

`stop` preserves application, Tailscale, and Caddy volumes. Never run
`docker compose down --volumes` during normal operation.

## Existing Host-Service Installation

Do not point the new stack at an existing live SQLite file and do not accept a
fresh empty Docker volume as a replacement for franchise data.

Before cutover:

1. stop transaction entry;
2. create and retain a verified application backup;
3. stop the old NSSM/Python/Caddy services;
4. review the permanent franchise code and every existing QR for global
   collisions;
5. settle, migrate, or explicitly exclude every legacy
   `PENDING_SYNC`/`SYNCING`/`FAILED` batch; the inventory baseline does not
   recreate historical transactions or Tally vouchers;
6. migrate the database into the named application volume using the approved
   cutover procedure;
7. initialize active `GENERATED`/`IN_STOCK` inventory before event 1;
8. verify Master ownership, cursor, and reports before reopening operations.

The deployment helper detects a legacy local database and refuses to silently
ignore it. Sold/issued history and pre-cutover reports require an explicit
migration decision. Connected database rollback additionally requires Master
cursor reconciliation; see [Backup and restore](backup-restore-guide.md).

The old `Setuora.exe`, Windows scripts, host Caddy, and NSSM files remain only
for examining/stopping an existing installation. They are not the supported
path for a new connected Lite deployment.

## Validation

From a development/release checkout:

```bash
python -m pytest -q
python -m compileall -q app
docker compose config --quiet
docker compose build setuora
```

Before live use, complete the
[production release checklist](production-release-checklist.md).
