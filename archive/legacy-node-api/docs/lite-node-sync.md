# Setuora Lite Node Synchronization

Status: application and self-hosted private-network MVP implemented; operational
acceptance pending.

Setuora Lite is the franchise-side transaction application. It owns purchase,
sale, return, issue, audit, QR generation/printing, local inventory history, and
physical dispatch/receipt workflows. Lite communicates with Setuora Master using
outbound HTTPS only. Master never requires an inbound route to a franchise.

The exact wire contract is
[Node Sync API v1](../../../Setuora-Master/docs/api/node-sync-v1.md). The system
boundary is defined in
[Master ADR-001](../../../Setuora-Master/docs/architecture/adr-001-master-lite-control-plane.md).
Lite's deployment boundary is defined in
[ADR-002](adr-002-lite-self-hosted-tailscale-egress.md).

The durable outbox, command inbox, worker, namespaced QR generation, and
partial/full transfer flow now exist. They are a single-process SQLite MVP, not
approval for an unbounded production rollout. Docker/Caddy LAN HTTPS and the
outbound-only Tailscale egress path are implemented. Credential operations,
restore reconciliation, existing-data cutover, and two-node failure/soak
testing remain gates.

## Edition Boundary

When `SETUORA_APP_MODE=lite`, Lite registers:

- franchise transaction and inventory routes;
- QR generation and printing;
- local reports and maintenance;
- the Master connection status page;
- outbound and inbound transfer pages;
- the in-process Master synchronization worker.

Lite mode does not register Tally settings or Tally-check routes and does not
start the direct Tally retry worker. The inherited direct-Tally compatibility
composition requires both `SETUORA_APP_MODE=legacy` and the explicit
`SETUORA_ALLOW_LEGACY_TEST_MODE=true` test gate. Never enable that gate on a
deployed franchise.

Lite does not:

- accept inbound WAN connections;
- connect to another Lite node or its database;
- select a different franchise identity in an event;
- call Tally while completing a local transaction;
- unlock dispatched stock because Master or the Internet is unavailable;
- execute arbitrary code, URLs, SQL, installers, or shell commands from Master.

## Configuration

Relevant `.env` settings:

```dotenv
SETUORA_APP_MODE=lite
FRANCHISE_CODE=FR01
MASTER_SYNC_ENABLED=true
MASTER_URL=https://setuora-master.<tailnet>.ts.net
MASTER_API_KEY=setuora-node.<key_id>.<secret>
MASTER_SYNC_INTERVAL_SECONDS=30
MASTER_REQUEST_TIMEOUT_SECONDS=15
MASTER_TLS_VERIFY=true
```

Franchise admins and super admins can update the same connection values from
`Master connection → Connection settings`. Frontend-managed values override
the deployment defaults and are written with owner-only permissions to
`data/master-connection.env` (or `/srv/setuora/data/master-connection.env` in
the supported container). The credential field is write-only: leaving it blank
keeps the stored credential, and its value is never rendered back into HTML.
The permanent franchise code becomes uneditable after the first outbox event.

Rules:

- `FRANCHISE_CODE` must equal the unique code enrolled in Master.
- the supported deployment requires Master's exact private
  `https://*.ts.net` URL;
- `MASTER_API_KEY` is the one-time key shown by Master for this franchise.
- the interval has an application minimum of 15 seconds;
- the request timeout has a minimum of 3 seconds;
- Python's normal HTTPS hostname and certificate verification remains active.

`MASTER_TLS_VERIFY` is currently reserved configuration and does not install a
custom unverified TLS context. Keep it `true`; there is no supported
skip-verification rollout.

Enabled Lite startup fails fast when the franchise code, HTTPS URL, or API key
is missing. The deployment helper additionally verifies that the credential's
authenticated `GET /api/v1/node` franchise code matches `FRANCHISE_CODE`. The
key lives in `.env` or the protected frontend-managed runtime file, so
production requires service-account filesystem ACLs, redacted logs/backups, an
approved secret transfer channel, and a tested rotation procedure.

## Local Event Outbox

`MasterOutboxEvent` is the durable upload record. Its SQLite
`AUTOINCREMENT` primary key is also the franchise-local event `sequence`.
SQLite is instructed never to reuse a deleted row ID.

Important fields are:

```text
id / sequence       monotonically increasing SQLite AUTOINCREMENT
event_id            UUID, globally unique
event_type          Node Sync v1 type
schema_version      1
aggregate_type      BATCH, TRANSFER, QR_REPLACEMENT, or RELOCATION
aggregate_id        stable local business identity
payload_json        frozen canonical {"events":[...]} request
payload_sha256      SHA-256 of payload_json
status              PENDING | SENDING | SENT | FAILED
attempts            delivery-attempt count
next_attempt_at     retry time, nullable
last_error          bounded safe message, nullable
sending_at          start of current attempt, nullable
sent_at             successful 2xx time, nullable
```

The service reserves the AUTOINCREMENT sequence, builds the complete v1 event
envelope with that sequence and a UUID, canonicalizes it with sorted compact
JSON, calculates SHA-256, and freezes the JSON and digest. A model guard rejects
later payload or digest mutation.

Submitted transaction effects and their event are inserted in the same database
transaction. In connected Lite mode the local batch becomes `PENDING_SYNC`; no
network or Tally call occurs while its inventory transaction is open.

`QR_ASSIGNMENT` has no v1 wire event of its own. A submitted assignment creates
a `STOCK_SNAPSHOT` event that registers the activated QR ownership at Master.

### Code-bound node initialization

Before the first normal outbox event, an admin can use `Master connection →
Initialize node` to freeze a one-time baseline bound to the permanent normalized
franchise code. A stocked node snapshots every active `GENERATED` and
`IN_STOCK` serial in deterministic chunks that each remain at or below both
5,000 items and 5 MiB of exact UTF-8 request-body data. A genuinely empty node
creates an itemless `HEARTBEAT` marker instead. Before either baseline is
created, Lite calls authenticated `GET /api/v1/node` and requires the exact
franchise code plus a pristine Master cursor (`last_sequence=0`,
`next_sequence=1`). Both baseline forms use
`reason_code=INITIAL_ENROLLMENT`, the ordered outbox, and Master identity
validation.

Every ordinary enqueue and every sync transport attempt fails closed unless the
configured franchise code has that durable marker. Changing the code after
initialization therefore blocks both local stock mutations that would emit
events and all transport. Initialization is locked as soon as any outbox event
exists and cannot be rerun by deleting or resequencing rows. A database that
already contains an ordinary event without the marker requires manual cursor
and inventory reconciliation.

This initializes live available stock; it does not reconstruct historical
`SOLD`, `ISSUED`, invalid, missing, or transaction/report history. Those records
need a separately reviewed historical migration if consolidated pre-cutover
analytics are required.

## Uploader

The in-process worker runs every configured interval and calls
`push_pending_events` before polling commands. It uploads one frozen event per
HTTP request to:

```text
POST <MASTER_URL>/api/v1/events
```

Behavior:

1. Select the oldest outbox row whose status is not `SENT`.
2. Stop if that row is not yet retryable; never let a later sequence overtake it.
3. Mark it `SENDING`, increment attempts, and commit before network I/O.
4. Send the exact frozen body with the Bearer key.
5. Validate that the 2xx acknowledgement contains the exact event UUID,
   sequence, and an advanced Master cursor, then mark it `SENT`.
6. Stop on the first failed attempt.

Every event is validated before it enters the outbox: no more than 5,000 items
and no more than 5 MiB in the exact frozen UTF-8 request body. An ordinary
business transaction that would exceed either limit is rolled back with an
instruction to split the transaction; Lite never commits an event that Master
must reject for size.

If the process stops during delivery, a `SENDING` row becomes recoverable after
the greater of 60 seconds or twice the request timeout. Recovery keeps the
original event ID, sequence, JSON, and hash.

Retry decisions:

| Failure | Current behavior |
|---|---|
| Network, timeout, OS error | Exponential retry, starting at 2 seconds and capped at 2,048 seconds |
| HTTP `408`, `425`, `429`, or 5xx | Same exponential retry |
| HTTP `401` or `403` | Exponential retry so a corrected/rotated credential can recover the frozen event |
| HTTP `409` with code `CONCURRENT_EVENT_CONFLICT` | Retry after 60 seconds and keep later events blocked |
| Other HTTP `409` or `412` | Store `FAILED` with bounded, redacted Master details; require manual reconciliation and block the stream |
| HTTP `400`, `411`, `413`, `415`, `422`, or other 4xx | Store `FAILED` with no next attempt and block the stream |
| Malformed or mismatched 2xx acknowledgement | Retry the same frozen event; never assume that an ambiguous response was not applied |

The worker currently uploads one event at a time, does not honor `Retry-After`,
and does not add jitter. It does validate the exact Master acknowledgement
before advancing. An authorized admin can integrity-check and reschedule the
same frozen failed event from the Master connection page after correcting an
external cause. The event body cannot be edited or skipped, so invalid business
data such as an unknown transfer destination still needs an audited
cancel/correction protocol. Do not repair sequence rows with ad-hoc SQL.

## Command Inbox and Poller

Lite polls:

```text
GET <MASTER_URL>/api/v1/commands?limit=100
```

Each command is frozen in `MasterInboxCommand`:

```text
command_id          unique Master identity
command_type        allowlisted type
schema_version      defaults to 1
payload_json        canonical immutable business payload
payload_sha256      SHA-256 of payload_json
status              RECEIVED | APPLIED | FAILED
attempts            local apply count
last_error          safe bounded message, nullable
received_at         first persistence time
applied_at          successful local commit time, nullable
```

Redelivery with the same command ID and payload returns the existing applied
result. The same ID with a changed type or payload is rejected as a conflict.

The current allowlist is:

- `TRANSFER_AVAILABLE` (also accepts the compatibility alias
  `TRANSFER_INCOMING`);
- `TRANSFER_RECEIPT` (also accepts the compatibility alias
  `TRANSFER_RECEIPT_STATUS`).

For each command, Lite applies and commits the local effect before sending:

```text
PATCH <MASTER_URL>/api/v1/commands/<command_id>
{"acknowledged":true}
```

If the PATCH response is lost, Master redelivers the unacknowledged command.
Lite sees its already-applied inbox record and safely repeats the ACK. If local
application fails, Lite persists `FAILED`, stops the current poll batch, and
does not acknowledge the command.

The MVP has no `REJECTED` or `DEFERRED` command ACK status and no command cursor.
Unsupported commands remain unacknowledged until software or operator action
resolves them.

## QR Namespace

In Lite mode, newly generated serials use the implemented form:

```text
<FRANCHISE_CODE>-<SERIAL_PREFIX>-<six-digit local sequence>
```

For example:

```text
FR01-SG020-000041
```

`SERIAL_PREFIX` is the selected prefix or product code. If it already equals or
begins with the franchise code, Lite does not prepend the code again.
Identifiers are normalized uppercase. The local database unique constraint and
retry loop prevent duplicates inside one Lite database; Master also has one
global serial identity and rejects ownership already assigned elsewhere.

In Lite mode, QR generation fails if `FRANCHISE_CODE` is empty or one of the
known placeholder values, even while Master synchronization is disabled. The
application may be opened for setup first, but the permanent franchise code
must be assigned before any labels are generated.

The QR contains only the serial string. It contains no URL, API key, customer
data, or price.

The following remain rollout gates:

- centrally controlled, immutable, non-reused franchise codes;
- collision scan and explicit import policy for legacy printed serials;
- tests proving restore or clone operations cannot allocate another site's
  namespace;
- an audited policy for any manual QR/replacement value.

## Local Transaction Flow

```text
User submits a transaction
  -> validate role, serial state, and business fields
  -> BEGIN local database transaction
       write batch/items and inventory history
       update serial state
       reserve outbox AUTOINCREMENT sequence
       write and freeze the Node Sync v1 event
     COMMIT
  -> show local completion with Master status pending
  -> worker uploads independently
```

A Master or WAN outage does not roll back a completed local transaction.
Outbox disk capacity, backup retention, and maximum permitted offline duration
are operational policies; the MVP does not automatically delete unsent events.

## Inter-Franchise Transfer

### Source dispatch

Source Lite:

1. creates an outbound draft with a UUID and destination franchise code;
2. permits only active local `IN_STOCK` serials;
3. prevents a serial from being reserved by two open outbound transfers;
4. on dispatch, atomically changes every selected serial to `IN_TRANSIT`, writes
   `TRANSFER_OUT` history, sets the transfer to `DISPATCHED`, and enqueues
   `TRANSFER_DISPATCHED`;
5. keeps the items unavailable even if Master is offline.

Master accepts the ordered event only when it owns those serials for the source
and the destination is an active different franchise. It then queues
`TRANSFER_AVAILABLE` for the destination.

### Destination receipt

When destination Lite applies `TRANSFER_AVAILABLE`, it:

- verifies the command is addressed to its configured franchise;
- creates one inbound transfer in `AWAITING_RECEIPT`;
- materializes missing products from the manifest and rejects a local
  same-code/different-identity product collision;
- materializes each manifest serial as `IN_TRANSIT`;
- rejects duplicate manifest serials and active/local serial collisions. A
  returning inactive `IN_TRANSIT` QR is reactivated only when completed
  outbound history and product identity both match.

The operator may scan only an unreceived serial from that manifest. Finalizing a
receipt atomically:

- changes all currently scanned items from `IN_TRANSIT` to `IN_STOCK`;
- writes `TRANSFER_IN` history;
- marks the transfer `PARTIALLY_RECEIVED` or `RECEIVED`;
- enqueues one `TRANSFER_RECEIVED` event containing that subset.

Master changes global ownership only for the accepted subset and queues a
`TRANSFER_RECEIPT` command back to the source. Source Lite marks those manifest
items received and makes their historical `IN_TRANSIT` serial rows inactive.
If a QR later returns to that franchise through another Master-authorized
transfer, Lite reactivates the existing row only when it is inactive,
`IN_TRANSIT`, tied to completed outbound history, and the incoming product
identity matches exactly.

The MVP supports partial and repeated-subset receipts and duplicate protection.
Damaged/rejected receipt dispositions, transfer cancellation/reversal, lost-item
exceptions, and operator reconciliation are not yet separate workflow states.

### Tally boundary

Lite does not choose Stock Journal, Godown, company, or ledger mapping. Master
may monitor transfers, but transfer posting to Tally remains blocked until the
real-company Stock Journal/Godown validation gate passes.

## Offline and Failure Behavior

| Condition | Lite behavior |
|---|---|
| Internet or Master unavailable | Local eligible workflows continue; durable outbox grows |
| Response lost after Master commit | Same event body is retried; Master returns its original ACK |
| Oldest event conflicts | Later sequences remain blocked |
| Incoming command unknown during outage | The linked inbound transfer cannot be received until polling succeeds |
| Source dispatched before outage | Serial remains `IN_TRANSIT` and unavailable |
| Destination receives a subset | Received subset becomes local stock; remainder stays `IN_TRANSIT` |
| Command ACK response lost | Applied inbox record makes ACK retry idempotent |
| Credential revoked | Upload/poll fails; local business data and outbox remain |
| Process restart during upload | Stale `SENDING` recovery retries the identical event |

The Master connection page shows enabled/configuration state, status counts,
recent events, recent commands, and the last successful event. It permits an
authorized admin to trigger one sync cycle. Full alerts for oldest backlog age,
credential expiry, clock skew, disk pressure, and sustained command failure are
still rollout work.

## Concurrency and Recovery Constraints

For the MVP:

- one Lite application process owns its SQLite database;
- the worker is an in-process asyncio task;
- sequence allocation relies on SQLite AUTOINCREMENT;
- strict oldest-first selection prevents intentional overtaking;
- no database lease prevents a second process from running another worker.

Do not run multiple Uvicorn workers or a second sync worker. A future
multi-process design requires durable leases and duplicate-worker tests.

A Lite database restore can contain outbox state older than Master's cursor.
Master exposes `GET /api/v1/node` with `last_sequence` and `next_sequence`, but
the current worker does not automatically reconcile a restore. Browser reset
and restore are therefore disabled while Lite sync is enabled. Automated,
audited cursor reconciliation and inventory re-enrollment remain production
gates.

## Acceptance Gates

Before accepting a franchise for production use:

- the Tailscale grant permits Lite to Master TCP 443 only, with no Lite-to-Lite
  rule, Serve, Funnel, subnet route, or host-published Tailscale proxy;
- LAN HTTPS is bound only to the approved private interface and the Caddy public
  root certificate is trusted only on approved staff devices;
- every franchise has a unique key and permanent franchise code;
- `.env`, backups, logs, and service ACLs are reviewed for secret leakage;
- transaction/outbox atomicity survives forced termination;
- duplicate upload, lost response, sequence gap, and conflict tests pass;
- command redelivery and lost-ACK tests pass;
- legacy QR collision/import tests pass;
- the one-time available-stock baseline is enrolled and verified, the node
  starts from an explicitly approved empty baseline, and any required
  pre-cutover sold/issued history has a reviewed migration;
- at least 24 hours of offline backlog replays in exact order;
- dispatch remains locked across restart and WAN outage;
- destination codes are selected from a validated directory and invalid
  dispatch has a tested cancel/correction protocol;
- destination partial/full/duplicate receipt and invalid-manifest tests pass;
- credential rotation/revocation and restored-database cursor procedures pass;
- disk pressure alerts without deleting unsent events;
- installer/update/rollback preserves queued event and command bodies;
- two real Lite nodes pass the failure pilot in the
  [Master remote-connectivity runbook](../../../Setuora-Master/docs/deployment/remote-franchise-connectivity.md).
