# Windows Franchise SFTP/Tally Topology

> Historical franchise-Tally design. For the current one-Tally-at-Master system, see [central Tally topology](central-tally-topology.md).

Setuora Lite is installed on the Windows server in each franchise. It is the
franchise-side participant shown in the synchronization architecture.

```text
Local Tally --HTTP/XML--> Setuora Lite --SFTP--> Master /inbox
                                                |
                                       central debtor/creditor DB
                                                |
Local Tally <--HTTP/XML-- Setuora Lite <--SFTP-- Master /outbox
                             |
                             `--SFTP /ack only after Tally import succeeds
```

## Gated cycle

1. If Master `/outbox` contains XML, Lite downloads the oldest file first.
2. Lite validates the bounded XML and sends it to the local Tally gateway.
3. If Tally reports an error or imports nothing, Lite records the failure and
   leaves the Master file unacknowledged. No new franchise export is uploaded.
4. After a successful Tally import, Lite atomically uploads the matching empty
   `.ack` file.
5. Only when `/outbox` and `/inbox` are clear does Lite export current ledgers
   under `Sundry Debtors` or `Sundry Creditors` and upload a new `.xml` file.
6. An unchanged export digest is not uploaded again.

Every upload is written as `.part` and renamed only after it is complete. The
Master SFTP host key is verified against an exact SHA256 pin before password
authentication. Lite needs outbound network access only; its local web port and
Tally port are never exposed to the Internet.
