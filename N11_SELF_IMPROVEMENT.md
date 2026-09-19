# N11 — Self-Improvement Evaluation Loop

N11 adds a bounded software-improvement control loop on top of the N9/N10 execution and approval boundaries.

## Flow

finding -> improvement proposal -> patch candidate -> patch review -> validation -> bounded revision -> approval-ready result

The new autonomous_agent.self_improvement.SelfImprovementLoop is intentionally side-effect free:

- patch candidates are data, not executable source mutations;
- patch review reuses the existing forbidden-path, size, manifest, and NUL-byte checks;
- validation is injected through a narrow interface so a future sandbox/LLM worker can run tests without widening the core trust boundary;
- revisions are capped at three attempts total;
- validator exceptions fail closed;
- success stops at READY_FOR_APPROVAL;
- no branch, commit, pull request, merge, deployment, billing, credential, or production action occurs inside the loop.

## Approval boundary

A successful candidate can be handed to the existing approval/PR workflow only after an explicit human approval is present. The existing draft_pr_automation.py and github_changes.py remain the source-write and PR boundary.

## Why this is N11 rather than a full autonomous coding agent

The repository now has the evaluation/revision control plane, but the patch generator and validator are deliberately injected. This keeps provider/model choice separate from safety policy and prevents an LLM response from becoming an implicit source-write capability.

## Validation

The regression suite covers:

1. successful candidate validation;
2. failed validation followed by a bounded revision;
3. hard revision budget;
4. forbidden patch rejection before validation;
5. redacted approval summaries.

CI must remain green before N11 can be considered merged.
