-- Durable immutable run-journal records for each worker claim generation.
-- Apply only after 001 and 002 with the approved migration role on a dedicated Preview DB.
BEGIN;

CREATE TABLE IF NOT EXISTS scout_state.run_journal (
    namespace text NOT NULL,
    mission_id text NOT NULL,
    task_id text NOT NULL,
    execution_id text NOT NULL,
    claim_generation bigint NOT NULL CHECK (claim_generation > 0),
    task text NOT NULL CHECK (length(task) BETWEEN 1 AND 4000),
    state text NOT NULL CHECK (state IN ('running', 'verified', 'partial', 'failed', 'blocked', 'recovery_required')),
    reason text NOT NULL CHECK (length(reason) <= 4096),
    attempts integer NOT NULL CHECK (attempts >= 0),
    result_count integer NOT NULL CHECK (result_count >= 0),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (namespace, task_id, claim_generation),
    FOREIGN KEY (namespace, mission_id) REFERENCES scout_state.missions(namespace, mission_id),
    FOREIGN KEY (namespace, task_id) REFERENCES scout_state.work_items(namespace, task_id)
);
CREATE INDEX IF NOT EXISTS scout_run_journal_mission_idx
    ON scout_state.run_journal (namespace, mission_id, recorded_at, claim_generation);

ALTER TABLE scout_state.schema_version
    DROP CONSTRAINT IF EXISTS schema_version_version_check;
UPDATE scout_state.schema_version
   SET version = 3, applied_at = clock_timestamp()
 WHERE singleton = 1 AND version = 2;
ALTER TABLE scout_state.schema_version
    ADD CONSTRAINT schema_version_version_check CHECK (version = 3);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM scout_state.schema_version WHERE singleton = 1 AND version = 3
    ) THEN
        RAISE EXCEPTION 'unsupported prior Scout state schema version';
    END IF;
END $$;

COMMIT;
