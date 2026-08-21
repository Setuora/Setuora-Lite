# Windows SFTP Release Checklist

- [ ] The installer runs on the supported Windows edition without Docker or WSL.
- [ ] The `Setuora-Lite` startup task survives a reboot.
- [ ] TCP 8000 is reachable only on the franchise private LAN.
- [ ] Tally is reachable locally and the exact company is selected.
- [ ] The franchise code matches the Master enrollment.
- [ ] The SFTP account is unique, chrooted, and has no shell or forwarding.
- [ ] The SHA256 Master SSH host-key fingerprint was verified out of band.
- [ ] A debtor and creditor upload/import/ack round trip passed.
- [ ] A failed Tally import leaves `/outbox` unacknowledged and blocks uploads.
- [ ] Duplicate/unchanged exports do not create repeated work.
- [ ] Database, `.env`, SFTP state, and Tally backup recovery were tested.
- [ ] Application, Tally, SQLite, and backup ports/files are not Internet-facing.
