-- =============================================================================
-- V001__schemas.sql
-- =============================================================================
-- GENERATED from the gold mapping by framework/generators/generate_ddl.py.
-- Do not edit by hand: change the spec and regenerate, or the next run
-- overwrites this file and the drift is silent.
--
-- Idempotent. CREATE OR ALTER means re-running converges rather than failing,
-- so a migration can be applied to an environment in any state.
-- =============================================================================

-- Both schemas the gold layer uses.
--   physical: star-schema tables, written by the gold notebooks
--   reporting: views Power BI binds to; no pipeline reads them

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'dbo')
    EXEC('CREATE SCHEMA [dbo]');

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'bi')
    EXEC('CREATE SCHEMA [bi]');

