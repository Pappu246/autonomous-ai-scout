# Gate 4 — External Connector Smoke

Gate 4 is live-verified only with legitimate read-only Gmail and Google Calendar OAuth access.

The workflow first runs the existing deterministic Gmail, Calendar, application, and document safety suites. It then requires:
- `SCOUT_GMAIL_ACCESS_TOKEN`
- `SCOUT_CALENDAR_ACCESS_TOKEN`

The live smoke uses the existing Gmail and Calendar connector classes with read-only API operations. It emits only bounded counts and evidence fingerprints; it does not print message bodies, event contents, or credentials.

Application and document mutation paths remain approval-gated. Their deterministic safety coverage is part of this gate, while account-backed evidence is limited to the services for which real credentials are supplied.

A successful workflow run is the evidence required to promote Gate 4 to LIVE-VERIFIED.