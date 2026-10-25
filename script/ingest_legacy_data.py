from pathlib import Path
import pandas as pd
# pyrefly: ignore [missing-import]
import duckdb


# ============================================================
# PROJECT CONFIGURATION
# ============================================================

script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent

csv_path = project_root / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"

database_path = project_root / "data" / "SupplyChainDB.duckdb"

table_name = "TBL_SC_FLEET_HIST_RAW"


print("=" * 60)
print("PROJECT CONFIGURATION")
print("=" * 60)

print(f"Project root : {project_root}")
print(f"CSV path     : {csv_path}")
print(f"Database     : {database_path}")


# ============================================================
# CHECK CSV
# ============================================================

if not csv_path.exists():
    raise FileNotFoundError(f"CSV file not found:\n{csv_path}")


# ============================================================
# LOAD CSV
# ============================================================

print()
print("=" * 60)
print("LOADING DATASET")
print("=" * 60)

print(f"Loading CSV from:\n{csv_path}")

df = pd.read_csv(csv_path)

print()
print("✅ CSV loaded successfully!")
print(f"Rows    : {len(df):,}")
print(f"Columns : {len(df.columns)}")


# ============================================================
# REQUIRED COLUMNS
# ============================================================

legacy_mapping = {
    "timestamp": "TS_UTC",
    "vehicle_gps_latitude": "V_LAT",
    "vehicle_gps_longitude": "V_LON",
    "iot_temperature": "IOT_TEMP_VAL_C",
    "cargo_condition_status": "CGO_COND_CD",
    "risk_classification": "RISK_CLS_TXT",
    "delay_probability": "DELAY_PROB_DEC",
    "port_congestion_level": "PRT_CNG_LVL",
    "route_risk_level": "RT_RSK_IDX",
}

missing_columns = [column for column in legacy_mapping if column not in df.columns]

if missing_columns:
    raise ValueError(
        "Missing required CSV columns:\n"
        + "\n".join(f"  - {column}" for column in missing_columns)
    )

print()
print("✅ All required CSV columns found.")


# ============================================================
# CREATE LEGACY DATASET
# ============================================================

df_legacy = df[list(legacy_mapping.keys())].copy()

df_legacy.rename(columns=legacy_mapping, inplace=True)

# Add ingestion flag
df_legacy["SYS_INGEST_FLAG"] = "Y"


print()
print("Legacy schema created.")

print()
print("Columns being stored:")

for column in df_legacy.columns:
    print(f"  - {column}")


# ============================================================
# CREATE DUCKDB DATABASE
# ============================================================

print()
print("=" * 60)
print("CREATING LOCAL DATABASE")
print("=" * 60)

print(f"Database: {database_path}")

# Make sure the data directory exists
database_path.parent.mkdir(parents=True, exist_ok=True)

# Connect to local DuckDB database
connection = duckdb.connect(str(database_path))


# ============================================================
# LOAD DATA INTO DUCKDB
# ============================================================

print()
print("Creating table...")

connection.execute(
    f"""
    CREATE OR REPLACE TABLE {table_name} AS
    SELECT *
    FROM df_legacy
    """
)

print("✅ Table created successfully.")


# ============================================================
# VERIFY DATA
# ============================================================

print()
print("=" * 60)
print("VERIFYING DATA")
print("=" * 60)

row_count = connection.execute(
    f"""
    SELECT COUNT(*)
    FROM {table_name}
    """
).fetchone()[0]

print(f"Rows in database: {row_count:,}")


print()
print("Sample records:")

sample = connection.execute(
    f"""
    SELECT *
    FROM {table_name}
    LIMIT 5
    """
).df()

print(sample.to_string(index=False))


# ============================================================
# BASIC DATA CHECK
# ============================================================

print()
print("Risk classification distribution:")

risk_distribution = connection.execute(
    f"""
    SELECT
        RISK_CLS_TXT,
        COUNT(*) AS record_count
    FROM {table_name}
    GROUP BY RISK_CLS_TXT
    ORDER BY record_count DESC
    """
).df()

print(risk_distribution.to_string(index=False))


# ============================================================
# CLOSE DATABASE
# ============================================================

connection.close()

print()
print("=" * 60)
print("INGESTION COMPLETED SUCCESSFULLY")
print("=" * 60)

print()
print(f"Database file:")
print(database_path)

print()
print(f"Table:")
print(table_name)
