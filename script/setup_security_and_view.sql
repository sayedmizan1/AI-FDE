-- ====================================================================
-- FDE ENTERPRISE SECURITY & SEMANTIC LAYER SCRIPT (DuckDB)
-- Purpose: Protect the legacy DB from LLM hallucinations and mutations
-- Database: SupplyChainDB.duckdb
-- ====================================================================

-- 1. Create a dedicated schema for our clean AI views
CREATE SCHEMA IF NOT EXISTS FDE_VIEWS;

-- 2. Create the Semantic View (Translating legacy column names to clean English)
CREATE OR REPLACE VIEW FDE_VIEWS.VW_ACTIVE_FLEET AS
SELECT 
    TS_UTC AS "Timestamp",
    V_LAT AS "Latitude",
    V_LON AS "Longitude",
    CAST(IOT_TEMP_VAL_C AS DOUBLE) AS "Current_Temperature_C",
    CGO_COND_CD AS "Cargo_Condition_Code",
    RISK_CLS_TXT AS "Risk_Classification",
    DELAY_PROB_DEC AS "Delay_Probability",
    PRT_CNG_LVL AS "Port_Congestion_Level",
    RT_RSK_IDX AS "Route_Risk_Index",
    SYS_INGEST_FLAG AS "System_Ingest_Flag"
FROM TBL_SC_FLEET_HIST_RAW;

-- ====================================================================
-- SECURITY ARCHITECTURE NOTE FOR DUCKDB:
-- ====================================================================
-- Unlike client-server SQL engines (e.g. MS SQL Server / Postgres) that
-- use `CREATE LOGIN`, `CREATE USER`, `GRANT`, and `DENY` statements,
-- DuckDB is an embedded database engine.
--
-- Security & mutation protection are enforced at the Python/App layer:
-- 1. Open connection in read-only mode:
--    `duckdb.connect("data/SupplyChainDB.duckdb", read_only=True)`
-- 2. Point AI Agent tools strictly to `FDE_VIEWS.VW_ACTIVE_FLEET`.
-- 3. Read-only mode prevents all INSERT, UPDATE, DELETE, and ALTER calls.
-- ====================================================================