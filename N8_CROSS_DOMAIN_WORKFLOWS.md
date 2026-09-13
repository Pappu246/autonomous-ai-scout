# N8 — Cross-Domain Workflow Engine

N8 adds deterministic composition of existing capabilities into bounded multi-step workflows.

## Design

`WorkflowTask[] → dependency validation → capability validation → topological plan → existing TaskPlan → existing Safe Executor`

The workflow layer does not create a second executor or permission system. Every task must name an existing registered capability, and the generated plan records the existing authorization and execution boundaries.

## Bounds

- Maximum 12 workflow tasks.
- Task names are bounded and unique.
- Dependencies must reference known tasks.
- Cycles are rejected.
- Every capability must already be explicitly granted.
- Risk is the maximum risk across the workflow.
- Planning only; execution remains delegated to the existing executor.
- Payment, billing, secrets, merge, deployment and destructive capabilities remain outside the safe autonomous capability set.
