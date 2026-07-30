# ADR-002: Self-Hosted Lite With Outbound-Only Tailscale Egress

## Status

Accepted — 2026-07-30

## Context

Setuora Lite runs at a franchise and must keep its transaction, inventory,
printing, scanning, and reporting workflows usable when Master or the Internet
is unavailable. It also has to synchronize its durable Node Sync v1 stream with
Setuora Master across unrelated franchise networks without public ingress,
router port-forwarding, or direct Tally access.

The existing host-Python, NSSM, and host-Caddy installation is Windows-specific
and does not enroll or persist a Tailscale identity. Copying Master's Tailscale
Serve topology would make the franchise UI depend on tailnet reachability and
would create an inbound Lite service, contrary to Lite's outbound-only trust
boundary.

## Options Considered

| Option | Benefits | Costs and risks |
| --- | --- | --- |
| Keep the host installer and install Tailscale separately | Smallest deployment change | Windows-specific, split lifecycle, easy to omit or misconfigure Tailscale, no reproducible cross-platform stack |
| Publish each Lite UI with Tailscale Serve | Private remote HTTPS and no local CA | Makes ordinary franchise access depend on tailnet enrollment, introduces inbound tailnet access to Lite, weakens offline operation |
| Docker Compose with LAN Caddy and an egress-only Tailscale proxy | One Linux/Windows deployment, persistent identities, LAN UI remains independent, no public or tailnet Lite listener | Requires Docker and local Caddy-CA trust; stores the long-lived Master node key in protected local configuration |

## Decision

Use one Docker Compose project per franchise with three services:

1. `setuora` runs exactly one Lite web process, stores SQLite and verified
   backups plus backup schedule overrides in a named volume, and exposes no
   database or Tally port.
2. `caddy` provides the existing LAN-only HTTPS user interface and persists its
   internal certificate authority. Staff devices trust only the exported public
   root certificate.
3. `tailscale` owns a persistent device identity tagged `tag:setuora-lite` and
   exposes an HTTP CONNECT proxy only inside the Docker network. Lite sends
   `MASTER_URL` requests through that proxy.

The Tailscale container does not configure Serve, Funnel, a subnet route, or a
host-published proxy port. The tailnet policy permits
`tag:setuora-lite -> tag:setuora-master` on TCP 443 and does not permit
Lite-to-Lite traffic.

The Lite application and Caddy do not depend on Tailscale health to start.
Tailscale or Master outages therefore leave LAN workflows available while the
durable outbox grows and retries later.

Every connection still has two independent identities:

- the Tailscale device/tag grants network reachability; and
- the franchise-specific `setuora-node.*` credential binds Node Sync requests
  to one permanent Master franchise record.

Initial setup verifies `GET /api/v1/node` through the Tailscale proxy and
requires the returned Master franchise code to equal `FRANCHISE_CODE`, binds
the enrollment marker to the returned node `public_id`, and verifies the exact
Master/Lite cursors before the node is accepted.

Tally remains entirely inside Setuora Master. Lite does not publish port 9000,
register Tally routes, start a Tally retry worker, or interpret a Node Sync
acknowledgement as Tally success.

## Trade-offs

- Docker Engine/Compose becomes a franchise-server prerequisite.
- LAN HTTPS uses Caddy's private CA, so each approved phone or workstation must
  receive and trust the exported public root certificate.
- The Tailscale bootstrap key is removed after enrollment, but the long-lived
  Setuora node credential must remain in the protected `.env` file and in
  encrypted configuration backups.
- A restored connected Lite database cannot safely resume until its outbox
  cursor is reconciled with `GET /api/v1/node`.
- SQLite, one web process, and one in-process sync worker remain a controlled
  pilot boundary rather than a horizontally scaled deployment.

## Consequences

### Positive

- Linux and Windows use the same self-hosted deployment and lifecycle.
- Franchise UI availability is independent of Master/Tailscale health.
- No Lite, Uvicorn, SQLite, Tally, or proxy endpoint is exposed to the public
  Internet.
- Tailscale identity, application data, and Caddy CA state survive upgrades.
- Master synchronization uses certificate-verified private HTTPS and the
  existing ordered/idempotent protocol without application feature changes.

### Negative

- Existing host-service installations require an explicit, backed-up database
  cutover. Starting a fresh Docker volume must never be mistaken for migrating
  the old database.
- The LAN bind address, hostname, firewall scope, CA distribution, franchise
  code, Master URL, and two bootstrap credentials are site-specific inputs.

### Mitigation

- The deployment preflight validates the site-specific contract without
  printing secrets.
- Setup refuses to silently bypass an existing local database.
- Setup verifies local database health, LAN HTTPS, Tailscale enrollment, and
  Master credential/franchise binding as separate checks.
- Stop and update operations preserve all named volumes.

## Revisit Triggers

Revisit this decision if franchise users must access Lite outside the LAN, if
the LAN can no longer distribute a private CA, if Lite moves beyond the
single-process SQLite pilot, or if a managed device platform replaces
franchise-hosted servers.
