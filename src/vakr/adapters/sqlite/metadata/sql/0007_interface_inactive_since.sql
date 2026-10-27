-- description: Metadata schema 7: store when each interface's unbroken inactive period began,
--   so the ten-day stale clock no longer restarts when an annotation or tag edit moves
--   `updated_at` (LIFE-003, Appendix E.6, E.26).
-- destructive: false
-- transactional: true
-- recovery: Adds one nullable column and fills it in one transaction; on failure nothing
--   changes and the database stays at schema 6. Fix the cause and rerun `vakr db migrate`, or
--   restore the backup `vakr db migrate` took first. Self-update's rollback restores the
--   metadata.db it backed up before the update, at schema 6 (E.14).
-- data-volume: Rewrites each INACTIVE or STALE interface row once (about 1,600 interfaces at
--   the MVP design point). Finishes in well under a second.
-- test-impact: MIG-002 (schema 6 upgrades with data intact, backfill rule); LIFE-003 stale
--   clock tests in tests/unit/inventory/test_stale_clock.py and the lifecycle contract suite.

-- `inactive_since` is the start of the unbroken INACTIVE/STALE period (vakr.logic.lifecycle's
-- `LifecycleRecord.inactive_since`), in integer microseconds UTC like every other timestamp.
-- It is set exactly while `lifecycle` is 'inactive' or 'stale', and only lifecycle changes
-- write it; the domain refuses any other combination, so no CHECK repeats the enum here.
ALTER TABLE interface ADD COLUMN inactive_since INTEGER;

-- Backfill. Schema 6 kept only the state, and vakr used `updated_at` as the start of the stale
-- clock. That is the best evidence the old rows hold, so already inactive or stale interfaces
-- start from it. An edit before this upgrade may have moved it later; the clock then runs a
-- little long once, and never restarts again. Every other row has no inactive period.
-- `version` is not bumped: the record's meaning is unchanged, and the change counter must not
-- make every collector re-plan because of an upgrade.
UPDATE interface SET inactive_since = updated_at WHERE lifecycle IN ('inactive', 'stale');
