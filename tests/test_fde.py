import os
import sys
import unittest
from pathlib import Path

# Ensure project root is in python path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.bootstrap import ensure_bootstrapped


class TestFDEApplication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Ensure database and dependencies are bootstrapped before testing."""
        ensure_bootstrapped()

    def test_bootstrap_database_exists(self):
        """Verify DuckDB database file exists and is accessible."""
        db_path = project_root / "data" / "SupplyChainDB.duckdb"
        self.assertTrue(db_path.exists(), f"Database not found at {db_path}")

    def test_query_telemetry_valid_query(self):
        """Verify query_telemetry_db tool executes safe SELECT on the semantic view."""
        from src.agent_tool import query_telemetry_db

        result = query_telemetry_db.invoke("SELECT TOP 2 Latitude, Longitude, Current_Temperature_C FROM FDE_VIEWS.VW_ACTIVE_FLEET")
        self.assertTrue("COLUMNS:" in result or "No records matched" in result)
        self.assertNotIn("SECURITY BLOCK", result)

    def test_query_telemetry_security_blocks_drop(self):
        """Verify that destructive SQL operations like DROP are strictly blocked."""
        from src.agent_tool import query_telemetry_db

        result = query_telemetry_db.invoke("DROP TABLE TBL_SC_FLEET_HIST_RAW")
        self.assertIn("SECURITY BLOCK", result)

    def test_query_telemetry_security_blocks_delete(self):
        """Verify that DELETE statements are blocked."""
        from src.agent_tool import query_telemetry_db

        result = query_telemetry_db.invoke("DELETE FROM FDE_VIEWS.VW_ACTIVE_FLEET WHERE 1=1")
        self.assertIn("SECURITY BLOCK", result)

    def test_query_telemetry_security_blocks_raw_table_access(self):
        """Verify direct access to raw legacy table is prohibited."""
        from src.agent_tool import query_telemetry_db

        result = query_telemetry_db.invoke("SELECT * FROM TBL_SC_FLEET_HIST_RAW LIMIT 1")
        self.assertIn("SECURITY BLOCK", result)
        self.assertIn("VW_ACTIVE_FLEET", result)

    def test_fetch_corridor_conditions(self):
        """Verify live corridor conditions tool returns expected schema structure."""
        from src.agent_tool import fetch_corridor_conditions

        result = fetch_corridor_conditions.invoke({"latitude": 33.77, "longitude": -118.19})
        self.assertTrue("LIVE CORRIDOR TELEMETRY" in result or "Corridor API Communication Failure" in result)

    def test_orchestrator_graph_compiled(self):
        """Verify LangGraph orchestrator graph is successfully compiled and runnable."""
        from src.orchestrator import fde_agent

        self.assertIsNotNone(fde_agent)
        self.assertTrue(hasattr(fde_agent, "invoke") or hasattr(fde_agent, "stream"))


if __name__ == "__main__":
    unittest.main()
