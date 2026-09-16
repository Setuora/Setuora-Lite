# Lite to Master with one central Tally

There is one Tally installation, reachable from Setuora Master. No Lite server needs a Tally gateway or a Tally company.

```text
Lite A (state A) ──┐
                  ├── outbound HTTPS /api/v1 ── Master (state C) ── local HTTP/XML ── Tally
Lite B (state B) ──┘                         │
                                    Master database and Tally queue
```

Each Lite uses a unique permanent franchise code and node credential. Operations are committed to Lite's SQLite database with a durable, ordered event. Its worker pushes unsent events to Master and polls for commands. Network errors retain the event for retry. Master authenticates and deduplicates events by identity and sequence.

Master stores network stock and franchise ownership separately from the Tally company. Supported purchase, receive, sale, and sales return events enter Master's pending voucher queue. The central Tally worker sends one XML voucher at a time to the configured Tally gateway and records the result. Lite's `SENT` status confirms Master delivery only; it does not confirm Tally import.

Before normal event delivery, an administrator must enroll the franchise at Master and initialize its Lite inventory baseline. If Master or the Internet is unavailable, Lite retains queued events, while Master retains previously accepted events and pending Tally batches.

The SFTP debtor/creditor document in this directory is historical. Its franchise Tally import/export cycle is not used by this topology.
