# Coral Cloud — wire protocol

`openapi.yaml` is the **public contract** between the `coral-cloud`
client SDKs (in [`../clients/`](../clients/)) and the private
`coral-cloud-server`. It is published here for transparency and as the
single source of truth for SDK authors.

See [ADR-019](../docs/ADR-019-cloud-architecture.md) for the architecture
and [THREAT_MODEL-cloud.md](../THREAT_MODEL-cloud.md) for the security
posture.

> **Status: design-stage.** No server implements this contract yet
> (Track CLOUD-2). The Python SDK skeleton is built and tested against an
> in-process fake that mimics these shapes; expect breaking changes until
> the server lands.

## At a glance

- **Auth:** `Authorization: Bearer ck_live_…` project API key.
- **List sessions:** `GET /v1/sessions`
- **Open a session:** `POST /v1/sessions/{id}/open` → returns a `wss`
  `cdp_url` you connect any CDP client to.
- **Close:** `POST /v1/sessions/handles/{handle}/close`
- **Remote login (capture):** `POST /v1/logins` → `live_view_url`, then
  poll `GET /v1/logins/{id}`.
- **Policy / audit:** `/v1/policies/{origin}`, `/v1/audit`.

The `OpenSession` response shape deliberately mirrors the local
product's `coral_open_session` so existing integrations port by swapping
where `cdp_url` comes from (`wss://…` instead of `ws://127.0.0.1:…`).

## Versioning

The protocol version (`info.version`) is independent of the SDK and
server versions. Breaking changes bump the path prefix (`/v1` → `/v2`).
