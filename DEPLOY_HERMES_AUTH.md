# Hermes remote access over Tailscale

This guide exposes the standalone Hermes dashboard only inside a Tailscale tailnet while preserving EvoNexus' authenticated iframe proxy.

## Security model

- The embedded EvoNexus iframe continues to use the server-side proxy and the normal EvoNexus login/RBAC boundary.
- Direct access to Hermes on port `9119` is available only when both `EVONEXUS_HERMES_USERNAME` and `EVONEXUS_HERMES_PASSWORD` are configured.
- Without both credentials, Hermes binds to `127.0.0.1`; direct remote access is unavailable.
- Tailscale connect/disconnect actions require EvoNexus `config:manage` permission and are written to the audit log.
- The Tailscale auth key is submitted transiently through the Integrations UI. EvoNexus does not persist it or return it in logs/errors.

## Environment

```env
EVONEXUS_HERMES_USERNAME=hermes-admin
EVONEXUS_HERMES_PASSWORD=<strong-plaintext-password>

# Optional; defaults to evonexus-hermes.
EVONEXUS_TAILSCALE_HOSTNAME=evonexus-hermes
```

`EVONEXUS_HERMES_PASSWORD` is a strong plaintext secret consumed by Hermes' Basic Auth integration; do not pre-hash it. Store it in the deployment platform's secret manager and never commit it.

The native Hermes API server is separate from the dashboard and is disabled by default. Enable it only when required:

```env
EVONEXUS_HERMES_API_ENABLED=true
EVONEXUS_HERMES_API_KEY=[REDACTED]
```

Startup fails closed when the API server is enabled without a key.

## Deployment

1. Configure the environment variables in the dashboard service.
2. Deploy an immutable image tag with `EVONEXUS_IMAGE_TAG=sha-<revision>`.
3. Preserve the `/var/lib/tailscale` volume so the node identity survives restarts.
4. Open EvoNexus, navigate to **Integrations → Network**, and submit an ephemeral/reusable Tailscale auth key according to your tailnet policy.
5. Do not publish port `9119` to the public internet. Reach it through the Tailscale IP or MagicDNS name.

The supplied Swarm dashboard image starts `tailscaled` in userspace-networking mode. A successful connect configures `tailscale serve` as a tailnet-only TCP forward from port `9119` to `127.0.0.1:9119`; the API fails closed and disconnects when that forward cannot be configured. Non-standard images must provide a compatible `tailscale` CLI and daemon/socket. The supplied Compose and Swarm manifests mount `evonexus_tailscale_state` at `/var/lib/tailscale`.

## Acceptance checks

1. Log in to EvoNexus and confirm the embedded Hermes UI and WebSocket chat work without a second login prompt.
2. Confirm an unauthenticated request to `/api/tailscale/status` returns `401`.
3. Confirm a non-admin role without `config:manage` receives `403` for connect/disconnect.
4. From a device on the tailnet, open `http://<tailscale-ip-or-magicdns>:9119/` in a private browser window.
5. Confirm Hermes requires the configured Basic Auth credentials and WebSocket chat remains connected after login.
6. Confirm direct access is unavailable after removing both Hermes auth variables and redeploying.
7. Confirm the EvoNexus audit log records successful Tailscale connect/disconnect actions without an auth key.

## Rollback

1. Remove `EVONEXUS_HERMES_USERNAME` and `EVONEXUS_HERMES_PASSWORD` to return Hermes to loopback-only binding.
2. Disconnect the node from **Integrations → Network** if tailnet access is no longer required.
3. Redeploy the previous immutable `sha-*` image tag if application rollback is necessary.

Do not delete the Tailscale state volume unless intentionally decommissioning the node identity.
