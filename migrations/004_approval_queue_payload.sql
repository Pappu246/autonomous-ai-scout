-- Preserve an approval-gated mission's original queue payload until it is
-- atomically restored when the one-time approval token is consumed.
BEGIN;

ALTER TABLE scout_state.missions
    ADD COLUMN IF NOT EXISTS approval_queue_payload jsonb
    CHECK (approval_queue_payload IS NULL OR jsonb_typeof(approval_queue_payload) = 'object');

ALTER TABLE scout_state.schema_version
    DROP CONSTRAINT IF EXISTS schema_version_version_check;
UPDATE scout_state.schema_version
   SET version = 4, applied_at = clock_timestamp()
 WHERE singleton = 1 AND version = 3;
ALTER TABLE scout_state.schema_version
    ADD CONSTRAINT schema_version_version_check CHECK (version = 4);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM scout_state.schema_version WHERE singleton = 1 AND version = 4
    ) THEN
        RAISE EXCEPTION 'unsupported prior Scout state schema version';
    END IF;
END $$;

COMMIT;
