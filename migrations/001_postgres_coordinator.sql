-- Preview candidate only. Review/backup/approve before applying to a dedicated
-- PostgreSQL database. Never run this migration against Production or an
-- unrelated Supabase project. The runtime role must not own this schema.
BEGIN;
CREATE SCHEMA IF NOT EXISTS scout_state;

CREATE TABLE IF NOT EXISTS scout_state.schema_version (
    singleton integer PRIMARY KEY CHECK (singleton = 1),
    version integer NOT NULL CHECK (version = 1),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO scout_state.schema_version (singleton, version) VALUES (1, 1)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS scout_state.submissions (
    namespace text NOT NULL,
    idempotency_key text NOT NULL,
    request_digest text NOT NULL CHECK (length(request_digest) = 64),
    mission_id text NOT NULL,
    task_id text NOT NULL,
    execution_id text NOT NULL,
    submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (namespace, idempotency_key),
    UNIQUE (namespace, mission_id),
    UNIQUE (namespace, task_id)
);

CREATE TABLE IF NOT EXISTS scout_state.missions (
    namespace text NOT NULL,
    mission_id text NOT NULL,
    task_id text NOT NULL,
    execution_id text NOT NULL,
    state text NOT NULL CHECK (state IN
        ('pending', 'running', 'recovery_required', 'verified', 'partial',
         'failed', 'cancelled', 'requires_approval', 'rejected', 'blocked')),
    requires_output boolean NOT NULL DEFAULT false,
    claim_generation bigint NOT NULL DEFAULT 0 CHECK (claim_generation >= 0),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (namespace, mission_id)
);

CREATE TABLE IF NOT EXISTS scout_state.work_items (
    namespace text NOT NULL,
    task_id text NOT NULL,
    mission_id text NOT NULL,
    execution_id text NOT NULL,
    state text NOT NULL CHECK (state IN
        ('pending', 'running', 'recovery_required', 'verified', 'partial', 'failed', 'cancelled')),
    claim_generation bigint NOT NULL DEFAULT 0 CHECK (claim_generation >= 0),
    worker_id text,
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    heartbeat_at timestamptz,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (namespace, task_id),
    FOREIGN KEY (namespace, mission_id) REFERENCES scout_state.missions(namespace, mission_id)
);
CREATE INDEX IF NOT EXISTS scout_work_pending_idx
    ON scout_state.work_items (namespace, available_at, task_id) WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS scout_work_running_idx
    ON scout_state.work_items (namespace, heartbeat_at) WHERE state = 'running';

CREATE TABLE IF NOT EXISTS scout_state.approvals (
    namespace text NOT NULL,
    action_id text NOT NULL,
    mission_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'approved', 'rejected', 'consumed')),
    token_digest text CHECK (token_digest IS NULL OR length(token_digest) = 64),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (namespace, action_id),
    FOREIGN KEY (namespace, mission_id) REFERENCES scout_state.missions(namespace, mission_id),
    CHECK ((state IN ('pending', 'rejected') AND token_digest IS NULL AND consumed_at IS NULL)
        OR (state = 'approved' AND token_digest IS NOT NULL AND consumed_at IS NULL)
        OR (state = 'consumed' AND token_digest IS NOT NULL AND consumed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS scout_state.audit_heads (
    namespace text NOT NULL,
    stream_id text NOT NULL,
    sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0),
    event_hash text NOT NULL DEFAULT '' CHECK (event_hash = '' OR length(event_hash) = 64),
    PRIMARY KEY (namespace, stream_id)
);
CREATE TABLE IF NOT EXISTS scout_state.audit_events (
    namespace text NOT NULL,
    stream_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_id text NOT NULL,
    event_kind text NOT NULL,
    execution_id text NOT NULL,
    previous_hash text NOT NULL CHECK (previous_hash = '' OR length(previous_hash) = 64),
    event_hash text NOT NULL CHECK (length(event_hash) = 64),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (namespace, stream_id, sequence),
    UNIQUE (namespace, event_id),
    FOREIGN KEY (namespace, stream_id) REFERENCES scout_state.audit_heads(namespace, stream_id)
);

CREATE TABLE IF NOT EXISTS scout_state.memories (
    namespace text NOT NULL,
    mission_id text NOT NULL,
    execution_id text NOT NULL,
    kind text NOT NULL,
    outcome text NOT NULL,
    source_event_id text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (namespace, mission_id, execution_id, kind),
    FOREIGN KEY (namespace, mission_id) REFERENCES scout_state.missions(namespace, mission_id),
    FOREIGN KEY (namespace, source_event_id) REFERENCES scout_state.audit_events(namespace, event_id)
);
COMMIT;
