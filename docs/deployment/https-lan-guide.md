# Private LAN Access

The current Windows pilot serves Setuora Lite over HTTP on TCP 8000 and creates
a Windows firewall rule limited to the **Private** profile. Use only a trusted,
isolated franchise LAN. Do not publish TCP 8000 through router/NAT rules.

If HTTPS or remote access is required later, place a separately reviewed Windows
reverse proxy in front of Lite, enable secure cookies, and update trusted hosts.
That is outside the current Windows-first pilot scope.
