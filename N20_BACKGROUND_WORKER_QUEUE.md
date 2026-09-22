# N20 — Background Worker + Scheduler + Queue

## Objective

Allow tasks to be accepted into durable storage and processed independently by a background worker, including simple future scheduling and recovery after worker restart.

## Architecture

```text
task request
    │
    ▼
AutonomousTaskCore.submit_background()
    │
    ▼
TaskQueueStore  ── durable JSON snapshot
    │
    ├── PENDING + available_at
    ├── RUNNING
    ├── SUCCEEDED
    ├── FAILED
    └── CANCELLED
    │
    ▼
BackgroundTaskWorker
    │
    ├── claim ready task
    ├── invoke execution handler
    ├── persist terminal state
    └── recover RUNNING tasks after restart
```

## Capability proof

Run:

```bash
python examples/n20_background_worker_demo.py
```

The demo submits two tasks to a durable queue, starts a background thread, and prints the queue state changing from pending to succeeded while the caller remains outside the worker loop.

## Scheduling

`available_at` is an ISO-8601 timestamp. A task is not claimable until its scheduled time is reached. N20 provides simple delayed scheduling; recurring/event-driven triggers are N39.

## Recovery

On worker startup, previously `RUNNING` queue entries are returned to `PENDING` and marked with a restart diagnostic. The original `execution_id` is preserved so N16 checkpoint/resume can continue the execution without inventing a new identity.

## Safety

- Queue state is written atomically.
- Duplicate `task_id` values are rejected.
- Empty tasks and missing identities are rejected.
- Only one worker is intended in N20; concurrent execution is N32.
- Queueing validates the task through the canonical task core before persistence.
- Queueing does not grant extra capabilities or bypass approval policy.

## Limitations after N20

Still deferred:

- universal tool discovery (N21);
- real browser/computer automation (N22);
- general OS/shell agent (N23);
- web research knowledge acquisition (N24);
- communication workflows (N25);
- concurrent workers / parallel DAG execution (N32);
- recurring and event-driven triggers (N39).

## Acceptance gate

N20 is VERIFIED when queue persistence, delayed scheduling, worker execution, restart recovery, and failure handling pass full CI, and the runnable background capability demo is present.
