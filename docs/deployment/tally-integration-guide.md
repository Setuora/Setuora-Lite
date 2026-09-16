# Tally and SFTP Integration

> Historical franchise-Tally procedure. The current Lite has no Tally connection; see [central Tally topology](../architecture/central-tally-topology.md).

Enable the Tally HTTP/XML gateway for the reviewed company, normally on
`127.0.0.1:9000`. Setuora Lite requests a read-only ledger collection filtered
to **Sundry Debtors** and **Sundry Creditors**.

Lite uploads to `/inbox` with a `.part` suffix and renames only after transfer.
Master consolidates the ledgers and publishes a complete **All Masters** import
envelope in `/outbox`.

Lite always processes `/outbox` before creating another export. It validates
the size and structure, posts the XML to Tally, and accepts the import only when
Tally reports at least one created or altered master. Only then does Lite upload
the matching empty `/ack/<file-stem>.ack`. An import error leaves the Master file
unacknowledged, pauses the cycle, and appears in **Admin → Tally SFTP**.

Before first production use, back up the Tally company and test a debtor and a
creditor round trip. Review Tally's Exceptions report after any failed import.
