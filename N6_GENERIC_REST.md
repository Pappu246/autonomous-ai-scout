# N6 — Generic REST API Connector

N6 adds a bounded generic REST capability for APIs that do not yet have a dedicated connector.

## Safety contract

- HTTPS only; explicit host allowlist is mandatory.
- URL userinfo and non-default ports are rejected.
- DNS resolution is validated by default and every resolved IPv4/IPv6 address must be publicly routable.
- Private, loopback, link-local, multicast, reserved, and unspecified destinations are rejected.
- GET/HEAD are read-only and may be autonomous only after the connector is explicitly enabled and the REST capability is granted.
- POST/PUT/PATCH/DELETE require explicit human approval and receive a deterministic idempotency key.
- Authorization, cookie, proxy-authorization, and set-cookie values cannot be supplied as normal request headers.
- Credential references are metadata only at this layer; credentials are never resolved or injected here.
- Request body, response, headers, redirects, timeout, and retry behavior are bounded.
- Redirects are revalidated against HTTPS, the same explicit host allowlist, and fresh DNS resolution.
- Safe-method retries are bounded and writes are never retried automatically.
- Transport is injected so tests do not require live external API calls.
- Secrets are redacted from returned headers, text, and JSON payloads.
- The connector must not be used to bypass dedicated Gmail, Calendar, or Web connectors.
- Billing, payment, production deployment, destructive operations, and secret-management remain outside this capability.

The connector remains disabled by default and requires an explicit API host allowlist plus legitimate credential configuration before being enabled. Generic REST does not resolve provider credentials itself and must not be wired around existing connector-specific auth boundaries.
