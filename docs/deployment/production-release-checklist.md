# Lite release checklist

- [ ] The Lite and Caddy startup tasks survive a reboot; Lite and Caddy listen on localhost only, and Tailscale Serve provides private HTTPS.
- [ ] Each Lite has a permanent, unique franchise code and its own Master node credential.
- [ ] The configured Master URL is an exact HTTPS origin with a valid certificate.
- [ ] Inventory initialization completes once and its baseline event is accepted by Master.
- [ ] A supported operation enters Lite's durable event queue and reaches Master in sequence.
- [ ] An Internet interruption retains pending events; delivery resumes after connectivity returns.
- [ ] A Master command is applied and acknowledged by the intended Lite only.
- [ ] Lite has no local Tally or SFTP synchronization worker running.
- [ ] Database, connection settings, and backup recovery have been tested.
