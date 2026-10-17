# Vercel + Azure deployment architecture (historical alternative)

> **Not the current Vercel Services Preview architecture.** PR #280's active candidate uses two Vercel container services and private Vercel Blob state; see [Vercel Services deployment](DEPLOYMENT_VERCEL_SERVICES.md). Do not treat the Azure steps below as evidence of current resources or apply them to Production without explicit authorization.

Autonomous AI Scout is a persistent Python service, not a static-only website. Mission Control owns a durable queue, worker thread, approval records, memory, and JSON audit files. Run that service in Azure Container Apps; use Vercel as the public, password-protected HTTPS gateway.

## Required runtime variables

Configure these as encrypted deployment secrets, never as source code:

- **Azure Container App**
  - `SCOUT_SERVER_TOKEN`: high-entropy shared bearer secret; the service rejects non-loopback binds without it.
  - `SCOUT_HOST=0.0.0.0`
  - `SCOUT_PORT=8000`
  - `SCOUT_WORKSPACE_ROOT=/app`

- **Vercel Production and Preview**
  - `SCOUT_API_ORIGIN`: the Azure Container App's HTTPS origin only, such as `https://<app>.<region>.azurecontainerapps.io`; do not include a path.
  - `SCOUT_SERVER_TOKEN`: the same secret configured on the Azure service. The gateway adds this header server-side; it is never returned to the browser.
  - `SCOUT_WEB_ACCESS_TOKEN`: a separate high-entropy access password for Mission Control users. The gateway sets an HttpOnly, Secure, SameSite cookie with a 12-hour lifetime.

Use a random URL-safe secret of at least 32 characters for both tokens, and use different values for the web access password and backend bearer token.

## Azure Container Apps requirements

1. Build and deploy the root `Dockerfile` to Azure Container Apps with external HTTPS ingress and target port `8000`.
2. Store `SCOUT_SERVER_TOKEN` as a Container Apps secret and reference it from the container environment. Do not put it in an image, repository file, public URL, or build log.
3. Mount an Azure Files share at **`/app/state`** before using the service for real missions. The application writes queue, mission, approval, memory, and audit files under this directory. A disposable container filesystem is not durable storage.
4. Keep the app at a single replica until the JSON-backed queue/state layer is replaced with a concurrency-safe shared store. Do not enable multi-replica autoscaling for this file-backed state.
5. Do not expose the service token to a browser or set it in a `NEXT_PUBLIC_*` or other client-visible variable.

The container listens on port 8000. The public-bind security check intentionally fails closed if `SCOUT_SERVER_TOKEN` is missing.

## Vercel setup

1. Create a Vercel project linked to `Pappu246/autonomous-ai-scout` after this deployment change is merged.
2. Select the Python runtime / automatic framework detection and keep the repository root as the project root.
3. Add the three Vercel environment variables listed above to Production and Preview. Add no token to client-side variables.
4. Deploy a Preview first. Verify the password page, sign-in, mission list, read-only mission submission, timeline, and logout before promoting to Production.

The `vercel.json` rules send the existing Mission Control UI and its required API routes through `api/proxy.py`. The proxy has a route allowlist, rejects non-HTTPS backend origins and redirects, caps request and response sizes, does not forward browser cookies upstream, and does not log credentials or task content. The health response removes the workspace path.

## Deployment prerequisites

This repository contains deployment configuration only; it does not create cloud resources automatically. Before real missions can run remotely, provision the Container Apps runtime, image registry, durable Azure Files volume, and logging resources. Configure all credentials as encrypted deployment secrets; never commit them to source code or bake them into the container image.

Production provisioning requires an authenticated Azure account with permission to create Container Apps, a Container Apps environment, a registry, storage, and logging resources.


Until Azure exists and the three deployment secrets are configured in Vercel, the Vercel gateway intentionally returns a setup error rather than pretending that a backend is connected.
