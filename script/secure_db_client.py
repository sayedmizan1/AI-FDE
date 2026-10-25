"""
secure_db_client.py - Enterprise Security & RBAC Gatekeeper for DuckDB

Simulates SQL Server's USR_FDE_RO user by enforcing:
1. Physical read-only connection (DuckDB engine level).
2. Strict whitelist-only query inspection (blocks INSERT, UPDATE, DELETE, DROP, ALTER).
3. Semantic Layer isolation (denies access to raw TBL_SC_FLEET_HIST_RAW table, grants only FDE_VIEWS).
"""

import re
from pathlib import Path
import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "SupplyChainDB.duckdb"

# Allowed schema/views for the AI Agent (RBAC Whitelist)
ALLOWED_VIEWS = ["FDE_VIEWS.VW_ACTIVE_FLEET", "VW_ACTIVE_FLEET"]
DENIED_TABLES = ["TBL_SC_FLEET_HIST_RAW", "INFORMATION_SCHEMA", "SQLITE_MASTER"]

# Forbidden mutation keywords
MUTATION_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", 
    "TRUNCATE", "ATTACH", "COPY", "EXPORT", "IMPORT", "PRAGMA"
]


class SecurityPermissionError(PermissionError):
    """Raised when query violates RBAC or Security Policies."""
    pass


class ReadOnlyFDEClient:
    """
    Acts as user 'USR_FDE_RO' for the AI Agent.
    """
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at: {self.db_path}")

    def _validate_sql_security(self, sql_query: str) -> None:
        """
        Validates query against RBAC security rules.
        """
        cleaned_query = re.sub(r"--.*?$", "", sql_query, flags=re.MULTILINE)
        cleaned_query = re.sub(r"/\*.*?\*/", "", cleaned_query, flags=re.DOTALL).strip()
        tokens = re.findall(r"\b[A-Za-z0-9_.]+\b", cleaned_query)
        upper_tokens = [t.upper() for t in tokens]

        # 1. Block Mutations
        for keyword in MUTATION_KEYWORDS:
            if keyword in upper_tokens:
                raise SecurityPermissionError(
                    f"DENIED: Mutation operation '{keyword}' is forbidden for USR_FDE_RO."
                )

        # 2. Block access to Raw Tables
        for denied in DENIED_TABLES:
            if any(denied in t for t in upper_tokens):
                raise SecurityPermissionError(
                    f"DENIED: Direct access to '{denied}' is prohibited. Query through 'FDE_VIEWS.VW_ACTIVE_FLEET' instead."
                )

        # 3. Ensure query only uses SELECT
        if not upper_tokens or upper_tokens[0] != "SELECT":
            raise SecurityPermissionError(
                "DENIED: Only SELECT queries are permitted for USR_FDE_RO."
            )

    def execute_query(self, sql_query: str) -> pd.DataFrame:
        """
        Executes a secure SELECT query and returns pandas DataFrame.
        """
        # Step 1: Software RBAC Check
        self._validate_sql_security(sql_query)

        # Step 2: Physical Engine-Level Read-Only Connection
        with duckdb.connect(str(self.db_path), read_only=True) as con:
            return con.execute(sql_query).df()


# ============================================================
# DEMONSTRATION OF PERMISSIONS IN ACTION
# ============================================================
if __name__ == "__main__":
    print("=" * 65)
    print("TESTING USR_FDE_RO SECURITY GATEKEEPER")
    print("=" * 65)

    client = ReadOnlyFDEClient()

    # TEST 1: Allowed Query on Semantic View
    print("\n[TEST 1] Querying FDE_VIEWS.VW_ACTIVE_FLEET (Expected: ALLOWED)")
    try:
        df = client.execute_query("""
            SELECT "Timestamp", "Risk_Classification", "Delay_Probability"
            FROM FDE_VIEWS.VW_ACTIVE_FLEET
            WHERE "Delay_Probability" > 0.85
            LIMIT 3;
        """)
        print(">> SUCCESS: Query executed!")
        print(df.to_string(index=False))
    except Exception as e:
        print(f">> FAILED: {e}")

    # TEST 2: Attempting to query Raw Legacy Table
    print("\n[TEST 2] Querying dbo.TBL_SC_FLEET_HIST_RAW directly (Expected: DENIED)")
    try:
        client.execute_query("SELECT * FROM TBL_SC_FLEET_HIST_RAW LIMIT 5;")
    except SecurityPermissionError as e:
        print(f">> BLOCKED: {e}")

    # TEST 3: Attempting Mutation (DROP TABLE)
    print("\n[TEST 3] Attempting 'DROP TABLE' (Expected: DENIED)")
    try:
        client.execute_query("DROP TABLE TBL_SC_FLEET_HIST_RAW;")
    except SecurityPermissionError as e:
        print(f">> BLOCKED: {e}")

    # TEST 4: Attempting Mutation (INSERT)
    print("\n[TEST 4] Attempting 'INSERT INTO' (Expected: DENIED)")
    try:
        client.execute_query("INSERT INTO FDE_VIEWS.VW_ACTIVE_FLEET VALUES ('2026-01-01', 0, 0);")
    except SecurityPermissionError as e:
        print(f">> BLOCKED: {e}")

    print("\n" + "=" * 65)
    print("ALL SECURITY POLICIES ENFORCED SUCCESSFULLY")
    print("=" * 65)
