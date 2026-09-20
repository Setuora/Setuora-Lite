# Private HTTPS access for Lite

Lite is available to staff at the `https://<lite-name>.<tailnet>.ts.net` address shown during setup. Each staff PC joins the same Tailscale network. Tailscale Serve supplies the trusted HTTPS certificate and limits access to permitted tailnet members. Browser camera scanning uses this HTTPS address.

Caddy runs as a Windows startup task and proxies only on a saved `127.0.0.1` port, usually 8080; the Python application also listens only on a saved `127.0.0.1` port, usually 8000. Setup chooses another free port when needed. Both are internal to the Lite computer. The installer removes the previous Lite LAN firewall rule and does not open inbound LAN ports. Caddy forwards the original HTTPS scheme to the application for form origin checks and security headers. Do not create router or public Funnel forwarding for Lite.

Use `setuora.bat` → **Check status** to check the local app, Serve route, and HTTPS health endpoint. If the HTTPS address fails after a reboot, use **Setup / repair** to restart Caddy and refresh the route. Tailscale sign-in and any required tailnet device approval need an account administrator.
