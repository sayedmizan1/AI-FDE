import os
import sys
from pathlib import Path

# Resolve project root
project_root = Path(__file__).resolve().parent.parent

def ensure_bootstrapped():
    """
    Zero-config bootstrap for production and fresh environments.
    Ensures DuckDB database and ChromaDB SOP vector stores are initialized
    automatically if not already present.
    """
    db_path = project_root / "data" / "SupplyChainDB.duckdb"
    raw_csv = project_root / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"
    chroma_dir = project_root / "data" / "chroma_db"
    sop_file = project_root / "data" / "policy" / "Cold_Chain_Incident_SOP_v2.md"

    # 1. Initialize DuckDB if missing
    if not db_path.exists():
        print(f"[Bootstrap] Initializing DuckDB from {raw_csv}...")
        try:
            import duckdb
            import pandas as pd

            db_path.parent.mkdir(parents=True, exist_ok=True)
            if raw_csv.exists():
                df = pd.read_csv(raw_csv)
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
                valid_cols = [c for c in legacy_mapping.keys() if c in df.columns]
                df_legacy = df[valid_cols].rename(columns=legacy_mapping)
                df_legacy["SYS_INGEST_FLAG"] = "Y"

                with duckdb.connect(str(db_path)) as conn:
                    conn.execute("CREATE OR REPLACE TABLE TBL_SC_FLEET_HIST_RAW AS SELECT * FROM df_legacy")
                    conn.execute("CREATE SCHEMA IF NOT EXISTS FDE_VIEWS")
                    conn.execute("""
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
                        FROM TBL_SC_FLEET_HIST_RAW
                    """)
                print(f"[Bootstrap] ✅ DuckDB database ready at {db_path}")
            else:
                print(f"[Bootstrap] ⚠️ Raw CSV not found at {raw_csv}")
        except Exception as e:
            print(f"[Bootstrap] ⚠️ Error initializing DuckDB: {e}")

    # 2. Initialize ChromaDB SOP Vectors if missing
    if not chroma_dir.exists() or not any(chroma_dir.iterdir()):
        print(f"[Bootstrap] Initializing ChromaDB vector store from {sop_file}...")
        try:
            if sop_file.exists():
                from langchain_chroma import Chroma
                from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
                from src.agent_tool import embeddings, COLLECTION_NAME

                chroma_dir.mkdir(parents=True, exist_ok=True)
                raw_text = sop_file.read_text(encoding="utf-8")
                
                headers_to_split = [("#", "Header_1"), ("##", "Header_2"), ("###", "Header_3")]
                md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split)
                header_docs = md_splitter.split_text(raw_text)

                text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=60)
                chunks = text_splitter.split_documents(header_docs)
                for chunk in chunks:
                    chunk.metadata["source_file"] = sop_file.name
                    chunk.metadata["file_format"] = ".md"

                vector_store = Chroma.from_documents(
                    documents=chunks,
                    embedding=embeddings,
                    collection_name=COLLECTION_NAME,
                    persist_directory=str(chroma_dir)
                )
                print(f"[Bootstrap] ✅ Indexed {len(chunks)} SOP chunks into ChromaDB at {chroma_dir}")
        except Exception as e:
            print(f"[Bootstrap] ⚠️ Error initializing ChromaDB: {e}")

if __name__ == "__main__":
    ensure_bootstrapped()
