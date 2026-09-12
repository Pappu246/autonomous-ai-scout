# N6 — Generic REST API Connector

N6 adds a bounded generic REST capability for APIs that do not yet have a dedicated connector.

## Safety contract

- HTTPS only; explicit host allowlist is mandatory.
- URL userinfo and non-default ports are rejected.
- Optional DNS resolution rejects private, loopback, link-local, multicast, reserved, and unspecified addresses.
- GET/HEAD are read-only and may be autonomous once the connector is explicitly enabled and the REST capability is granted.
- POST/PUT/PATCH/DELETE require explicit human approval and receive a deterministic idempotency key.
- Authorization, cookie, proxy-authorization, and set-cookie values cannot be supplied as normal request headers.
- Request body, response, headers, redirects, timeout, and retry behavior are bounded.
- Redirects are revalidated against the same host allowlist.
- Transport is injected so tests do not require live external API calls.
- Secrets are redacted from returned headers/body text.
- The connector must not be used to bypass dedicated Gmail, Calendar, or Web connectors.
- Billing, payment, production deployment, destructive operations, and secret-management remain outside this capability.

The connector remains disabled by default until an explicit API host allowlist and legitimate credential configuration are supplied.
