# Lite backup and recovery

Back up the Lite SQLite database, `.env`, `data/master-connection.env`, and backup settings together. The database contains the durable outbound event sequence and inbound command journal. Store a copy off the franchise server.

Before restoring, stop Lite and record the last event sequence that Master accepted for this franchise. Restore matching configuration and database files together, then verify the franchise code and node credential before starting Lite. A restored database behind Master's accepted sequence requires reconciliation; do not delete or replay records by hand. Test the connection and compare Lite pending events with Master's franchise event history before returning to normal use.

Tally backup and recovery occur at Master. The old SFTP exchange state is relevant only to a legacy franchise-Tally installation.
