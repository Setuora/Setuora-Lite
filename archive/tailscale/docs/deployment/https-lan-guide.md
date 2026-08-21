# LAN HTTPS Guide

Phone camera access requires a secure browser context. The supported Compose
deployment therefore keeps the Lite UI on franchise LAN HTTPS through Caddy:

```text
Phone browser
  -> https://<reserved-private-address-or-LAN-name>
  -> Caddy container
  -> Setuora container:8000
```

This is independent of Tailscale. Lite's Tailscale container is an
outbound-only proxy to Master and does not configure Serve or Funnel.

## Site Inputs

Set during `python deploy.py setup`:

```dotenv
SETUORA_LAN_BIND_ADDRESS=192.168.1.20
SETUORA_LAN_HOSTNAME=192.168.1.20
SETUORA_HTTP_PORT=80
SETUORA_HTTPS_PORT=443
TRUSTED_HOSTS=192.168.1.20,127.0.0.1,localhost
SESSION_COOKIE_SECURE=true
```

Prefer a router-reserved private address. A LAN DNS name is also valid when
every staff device resolves it to that address. Bind only to the private
interface and scope the host firewall to approved local subnets. Do not
port-forward TCP 80/443 from the public Internet.

## Install the Public Root Certificate

After setup:

```bash
python deploy.py export-ca
```

Install the exported public root certificate as a trusted CA on each approved
phone or workstation, then open the LAN HTTPS URL printed by setup. Keep
individual application logins and role permissions; trusting the LAN CA does
not authenticate a Setuora user.

The `setuora-lite_caddy-data` volume contains the private CA. Back it up as a
secret when certificate continuity is required, but never distribute it. Only
the exported root certificate is public.

## Verification

From an approved LAN device:

1. open the HTTPS URL without a certificate warning;
2. sign in;
3. confirm the response includes HSTS and the application security headers;
4. grant camera permission and scan a test QR;
5. confirm the same URL is unreachable from an unapproved/public interface.

Use:

```bash
python deploy.py status
python deploy.py logs caddy
```

if Caddy is unavailable. A `502` response usually means Caddy is running but
the Lite container is not healthy.

## Offline Behavior

Caddy and Lite do not depend on Tailscale health. An Internet/Master outage or
an offline reboot must still restore LAN HTTPS and local transaction entry. The
Master connection page may show a growing outbox until connectivity returns.
