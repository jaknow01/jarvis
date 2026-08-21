# Local Server / Hosting Setup

Where Jarvis is (or will be) hosted for the always-on services (Messenger
webhook, scheduler). This documents the owner's home-server environment so the
deployment/webhook work can target it directly instead of assuming a cloud box.

## Hardware / network

- **Host:** Raspberry Pi (home server). This is where the always-on Jarvis
  service will run.
- **LAN IP:** static (Pi has a fixed local IP on the home network).
- **Public IP:** yes — the owner has a real public IP (confirmed indirectly: a
  self-hosted VPN server works, which requires reachability from outside). To be
  double-checked whether it is static or dynamic; if dynamic, add Dynamic DNS
  (e.g. DuckDNS) so a domain always points at the current address.
- **Router:** owner has full admin control → **port forwarding is available**
  (can forward e.g. 443/tcp to the Pi).

## What's already in place

- Public reachability (VPN server proves inbound works).
- Static LAN IP on the Pi.
- Full router control for port forwarding.

## What's still missing

- **TLS certificate.** No valid HTTPS cert yet. Meta's Messenger webhook
  Callback URL requires HTTPS with a CA-signed cert (self-signed is rejected).
  **Chosen path: free DDNS domain via DuckDNS + Let's Encrypt.** DuckDNS gives a
  free `*.duckdns.org` subdomain that always points at the current public IP
  (also covers the case the IP turns out to be dynamic); the cert is issued with
  certbot's **DNS-01 challenge** against DuckDNS (no need to expose port 80).
  **TODO: instruct the owner through DuckDNS signup + certbot issuance** once the
  webhook code is in place.
- The DuckDNS subdomain + token (to be created by the owner).

## Implication for the Messenger webhook

Because the home server has a public IP + port forwarding, the webhook can point
**straight at the Pi** in production — no ngrok/tunnel strictly required. ngrok
is only a dev convenience. The one remaining prerequisite is the TLS cert +
domain. See [MESSENGER.md](MESSENGER.md).

Deployment shape:
`Meta → https://<domain> (443, forwarded on router) → Pi → Jarvis webhook (FastAPI)`
