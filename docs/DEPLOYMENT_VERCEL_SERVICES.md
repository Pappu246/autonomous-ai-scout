# Vercel Services deployment

Autonomous AI Scout is exposed through a password-protected gateway container; the existing persistent Python mission server runs as a separate internal container service in the same Vercel project.

## Service layout

- Public routes (`/`, `/login`, `/logout`, `/health`, mission and approval APIs) route to the gateway service.
- The gateway uses the existing bounded proxy handler and runs it through Python's threaded HTTP server.
- The gateway has a route/method allowlist, validates the backend HTTPS origin, forwards the backend bearer token server-side, rejects redirects, and limits request/response sizes.
- A Vercel service binding injects the internal runtime URL as `SCOUT_API_ORIGIN`. Do not hardcode the internal deployment URL.
- The runtime container listens on `PORT` (default 3000) and requires `SCOUT_SERVER_TOKEN` because it binds to `0.0.0.0`.

## Environment configuration

Configure these as encrypted environment variables on the Vercel project for Preview and Production:

- `SCOUT_SERVER_TOKEN`: strong random backend bearer token; the same value is available to the gateway and runtime containers.
- `SCOUT_WEB_ACCESS_TOKEN`: separate strong random password used by the Mission Control login page.

`SCOUT_API_ORIGIN` is supplied by the service binding. Do not hardcode it or expose either token to browser code. The gateway uses an HttpOnly, Secure, SameSite=Strict cookie and never returns the backend token to the browser.

## State durability boundary

Mission queue, approval records, memory, and audit files currently live under `/app/state`. This deployment runs the existing file-backed store but does not configure a durable shared disk. State can be lost when the runtime container is replaced or redeployed. Treat this as a Preview until a persistent store is mounted or the state layer is migrated to a durable database/object store and verified with a restart/redeploy test.

## Deployment checks

Before production release, verify the exact deployment commit and test:
1. Login and logout.
2. Mission list, task submission, and worker state transitions.
3. Timeline, memory, and approval inbox.
4. Unauthorized requests fail closed; tokens are not returned to client responses.
5. State survives a runtime restart/replacement once durable storage is configured.
