# Lite user manual

Staff join the franchise tailnet and use Lite's private HTTPS address to record stock movements, sales, receipts, and transfers. Tally runs at Master, not on the Lite server.

Administrators configure **Admin → Master connection** with the permanent franchise code, Master's HTTPS origin, and this franchise's node credential. After enrollment, initialize inventory once and run the first sync. The connection page shows pending, sent, and failed events without showing credentials or event contents.

Lite records events locally and retries delivery after a network interruption. `SENT` means Master accepted an event; it does not mean the related Tally voucher succeeded. Review central voucher results and exceptions on Master. Do not reset or restore a connected Lite database without preserving and reconciling its event queue.
