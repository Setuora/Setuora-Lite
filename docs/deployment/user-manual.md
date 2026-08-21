# User Manual

Administrators configure the local Tally company under **Admin → Settings** and
the Master exchange under **Admin → Tally SFTP**. The Tally SFTP screen shows
whether synchronization is enabled, the latest exchange state, and any Tally or
SFTP error without displaying credentials or XML contents.

Normal synchronization needs no file handling by staff. Lite exports current
debtors/creditors, uploads them to Master, downloads the consolidated XML,
imports it into Tally, and acknowledges it. If the screen shows a failed import,
leave synchronization paused, review Tally's Exceptions report and company
selection, correct the cause, then use **Sync now**.

Back up Tally before the first live round trip and follow the backup guide before
restoring the Setuora database or changing the franchise SFTP identity.
