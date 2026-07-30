# Tally Boundary for Setuora Lite

Setuora Lite does not connect to Tally.

In the supported `SETUORA_APP_MODE=lite` deployment:

- Tally settings and Tally Check routes are not registered;
- the direct Tally retry worker does not start;
- port `9000` is not published, proxied, or permitted through Tailscale;
- local transactions commit inventory plus an immutable Master outbox event;
- a Master event acknowledgement means the central event and projection were
  committed; it does not mean Tally accepted a voucher.

Setuora Master owns the company configuration, master validation, durable Tally
queue, retry history, posting, and reconciliation. Configure and validate those
only on the protected Master deployment:

- [Master Tally integration guide](../../../Setuora-Master/docs/deployment/tally-integration-guide.md)
- [Node Sync API v1](../../../Setuora-Master/docs/api/node-sync-v1.md)

Lite must still collect the product, party, GST, rate, and exact Tally stock
item metadata required by the Node Sync contract. Master uses that frozen event
data later when it builds eligible accounting work.

Master currently uses one active global Tally company configuration.
Per-franchise company/Godown mapping, inter-franchise Stock Journal design, and
real-company acceptance remain Master-side production gates. Do not re-enable
direct Tally in Lite as a workaround.

The inherited direct-Tally code is retained only for regression and migration
tests. It requires both `SETUORA_APP_MODE=legacy` and
`SETUORA_ALLOW_LEGACY_TEST_MODE=true`; neither setting is supported on a
deployed franchise.
