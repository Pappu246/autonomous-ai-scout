# Gate 5 — GitHub Administration Readiness

Gate 5 requires a credential with GitHub repository Administration permission because branch-protection APIs are outside the permissions of the connected control-plane used in this session.

Required secret: `SCOUT_GITHUB_ADMIN_TOKEN`.

The workflow:
- reads current `main` branch protection;
- optionally applies a hardened configuration only with `apply_protection=true`;
- verifies required `test` status checks, at least one pull-request approval, admin enforcement, no force pushes, and no branch deletion;
- reads current repository rulesets.

The workflow defaults to read-only. Configuration changes require an explicit workflow input.