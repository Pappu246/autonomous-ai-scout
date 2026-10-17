# Vercel Services deployment (Preview candidate)

Autonomous AI Scout uses a public gateway container (`gateway_next`) and an internal Python mission runtime container (`runtime_next`) in one Vercel **Services** project. This document describes the candidate on PR #280 as inspected on 2026-10-17; it is **not** a Production-release assertion. See the dated reports in `reports/` for current audit evidence and owner-only release gates.

## Service and access layout

- `vercel.json` rewrites only `/`, `/login`, `/logout`, `/health`, the mission APIs, and the approval APIs to the gateway. Other paths are not intended public routes. The gateway runs `autonomous_agent.vercel_gateway_server` from `Dockerfile.vercel`; the private runtime runs `autonomous_agent.server` from `Dockerfile`.
- A Vercel service binding supplies the internal HTTPS runtime origin as `SCOUT_API_ORIGIN`. Do not hardcode it or place a backend bearer token in browser code. The gateway rejects backend redirects, limits sizes and paths, and adds `SCOUT_SERVER_TOKEN` server-side. The runtime requires that token on non-loopback binds.
- **Preview** access is gated by **Vercel Deployment Protection/SSO**. The Preview gateway's `GET /login` redirects to `/` after the edge gate; `POST /login` rejects credentials. It must not show the application's workspace password form on the flagged Preview hostname. Test actual edge protection on the exact immutable deployment and branch alias, including historical deployments and any bypass grants; `VERCEL_ENV=preview` in Python is not proof that the edge is protected.
- **Non-Preview fallback only:** the separate `SCOUT_WEB_ACCESS_TOKEN` signs an opaque 12-hour Secure/HttpOnly/SameSite=Strict session cookie. This app login is not used to authorize Preview. Logging out clears the browser cookie; stateless sessions cannot revoke a stolen cookie before expiry without token rotation.

## Environment and state

- `SCOUT_SERVER_TOKEN` is a credential shared by gateway and runtime **within** an environment. Separate environments should have separate values under an approved migration plan. `SCOUT_WEB_ACCESS_TOKEN` is needed only where the fallback app login is enabled, not for current Preview SSO. `SCOUT_API_ORIGIN` comes from the service binding.
- The runtime requires `BLOB_READ_WRITE_TOKEN` for hosted, private, cross-instance Blob-backed synchronization of mission metadata, queue, approvals, audit, memory, and one-time approval claims. Without it, hosted Mission Control must not accept stateful missions or start its worker. The source checkout's `state/` JSON fixtures are excluded from container images by `.dockerignore`.
- **Do not edit these tokens directly as ordinary configuration.** The 2026-10-17 metadata audit found all three classified Config across Production/Preview/Development. They must be handled as credentials; the Blob entry is storage-linked and may be integration-managed. Owner approval, ownership preflight, provider-supported rotation, environment scoping, backup, rollback and Preview redeployment are required before changing them. See [`VERCEL_ENVIRONMENT_AUDIT.md`](../reports/VERCEL_ENVIRONMENT_AUDIT.md).
- Private Blob synchronization improves state visibility but does not by itself prove exactly-once side effects. The current preview-state key namespace is environment-wide (not branch-specific), a 120-second stale Blob lock cannot be fenced with the SDK's unconditional `delete`/`put`, and running-task recovery has no heartbeat. Do not run consequential missions across multiple Preview branches/replicas until an owner-approved isolation and reconciliation plan is implemented and fault-tested.
- `SpecialistProviderRouter.route()` only **selects** a potential provider; this Mission Control path does not invoke a generative model for narrative analysis. A file listing or tool success cannot substantiate a requested project review. Such requests now report `partial` when no model-written outcome is produced, including when a provider route was merely selected.

## Mandatory validation before Production

1. Confirm the exact Git SHA of the immutable Preview deployment and resolve the branch alias to it. Inspect the Preview protection/bypass settings, old reachable deployments, all public routes, headers, redirects and methods.
2. In an authorized authenticated SSO browser session, submit a harmless read-only task; verify ID, refreshed list, worker execution, terminal state, audit/timeline and memory on another runtime instance. Test a simulated transient storage failure and recovery, and verify approval-required operations remain blocked until explicitly approved.
3. Check integration-managed Blob connection health and persistence after container replacement. Verify environment snapshot timestamps follow any approved secret migration. Inspect runtime error logs without disclosing values.
4. Confirm Google Safe Browsing status separately using the **exact flagged hostname** and Search Console Security Issues owner review. A successful build or protected login does not clear a Google warning.
5. Obtain independent security review, owner authorization for any credential/Production changes and a documented rollback window. Never promote this Preview or merge PR #280 automatically.

The historical Azure Container Apps alternative is documented separately in `DEPLOYMENT_VERCEL_AZURE.md`; it is not the Vercel Services architecture presently under review.
