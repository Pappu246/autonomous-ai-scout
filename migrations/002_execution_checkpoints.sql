-- Add PostgreSQL-backed, claim-fenced execution checkpoints. Apply only after
-- 001_postgres_coordinator.sql, using an owner-approved migration role.
BEGIN;

CREATE TABLE IF NOT EXISTS scout_state.execution_checkpoints (
    namespace text NOT NULL,
    task_id text NOT NULL,
    mission_id text NOT NULL,
    execution_id text NOT NULL,
    claim_generation bigint NOT NULL CHECK (claim_generation > 0),
    schema_version integer NOT NULL CHECK (schema_version = 2),
    task_digest text NOT NULL CHECK (length(task_digest) = 64),
    plan_digest text NOT NULL CHECK (length(plan_digest) = 64),
    authorization_digest text NOT NULL CHECK (length(authorization_digest) = 64),
    state text NOT NULL CHECK (state IN ('running', 'verified', 'partial', 'failed', 'blocked')),
    completed_step_ids jsonb NOT NULL CHECK (jsonb_typeof(completed_step_ids) = 'array'),
    total_attempts integer NOT NULL CHECK (total_attempts >= 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (namespace, task_id, execution_id, claim_generation),
    FOREIGN KEY (namespace, mission_id) REFERENCES scout_state.missions(namespace, mission_id),
    FOREIGN KEY (namespace, task_id) REFERENCES scout_state.work_items(namespace, task_id)
);

ALTER TABLE scout_state.schema_version
    DROP CONSTRAINT IF EXISTS schema_version_version_check;
UPDATE scout_state.schema_version
   SET version = 2, applied_at = clock_timestamp()
 WHERE singleton = 1 AND version = 1;
ALTER TABLE scout_state.schema_version
    ADD CONSTRAINT schema_version_version_check CHECK (version = 2);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM scout_state.schema_version WHERE singleton = 1 AND version = 2
    ) THEN
        RAISE EXCEPTION 'unsupported prior Scout state schema version';
    END IF;
END $$;

COMMIT;
