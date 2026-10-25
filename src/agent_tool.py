import os
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

# Suppress symlink & TF import conflicts
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["USE_TF"] = "0"

import duckdb

from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma

# ==========================================
# 1. ENVIRONMENT & DYNAMIC INDEX ATTACHMENT
# ==========================================
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent  

load_dotenv(dotenv_path=project_root / ".env")

# DuckDB Database Path
DB_PATH = project_root / "data" / "SupplyChainDB.duckdb"

# ChromaDB Vector Store Path
CHROMA_DB_DIR = project_root / "data" / "chroma_db"

EMBEDDINGS_MODEL_SETTING = os.getenv("Embeddings_model", "LOCAL").strip().upper()

def get_cached_huggingface_embeddings(model_name: str):
    """
    Loads and caches the HuggingFace embedding model into RAM.
    """
    try:
        import streamlit as st
        if st.runtime.exists():
            @st.cache_resource(show_spinner=False)
            def _load_model(name: str):
                from langchain_huggingface import HuggingFaceEmbeddings
                return HuggingFaceEmbeddings(
                    model_name=name,
                    model_kwargs={'device': 'cpu'},
                    encode_kwargs={'normalize_embeddings': True}
                )
            return _load_model(model_name)
    except Exception:
        pass

    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={'device': 'cpu'},
        encode_kwargs={'normalize_embeddings': True}
    )

if EMBEDDINGS_MODEL_SETTING == "OPENAI":
    print("[MODE] Connecting to OpenAI Embeddings Vector Store...")
    embeddings = OpenAIEmbeddings()
    COLLECTION_NAME = "fde-sop-openai"
else:
    local_model_target = os.getenv("Local_Embedding_Model", "BAAI/bge-m3").strip()
    print(f"[MODE] Connecting to Local [{local_model_target}] ChromaDB Store...")
    embeddings = get_cached_huggingface_embeddings(local_model_target)
    COLLECTION_NAME = "fde-sop-local"

# Initialize ChromaDB Vector Store
vector_store = Chroma(
    collection_name=COLLECTION_NAME,
    embedding_function=embeddings,
    persist_directory=str(CHROMA_DB_DIR)
)
retriever = vector_store.as_retriever(search_kwargs={"k": 2})


# ==========================================
# 2. CORE FDE AGENT TOOLS
# ==========================================

@tool
def query_telemetry_db(sql_query: str) -> str:
    """
    Executes a SQL SELECT query against the FDE_VIEWS.VW_ACTIVE_FLEET view in the SupplyChain telemetry database.
    Columns available:
    Timestamp, Latitude, Longitude, Current_Temperature_C, Cargo_Condition_Code,
    Risk_Classification, Delay_Probability, Port_Congestion_Level, Route_Risk_Index.
    Only SELECT queries are authorized.
    """
    if not DB_PATH.exists():
        try:
            from src.bootstrap import ensure_bootstrapped
            ensure_bootstrapped()
        except Exception:
            pass

    if not DB_PATH.exists():
        return f"Database Error: DuckDB database not found at {DB_PATH}"

    # Strip comments
    cleaned_query = re.sub(r"--.*?$", "", sql_query, flags=re.MULTILINE).strip()

    # RBAC & Safety Validation
    upper_query = cleaned_query.upper()
    if not upper_query.startswith("SELECT") and not upper_query.startswith("WITH"):
        return "SECURITY BLOCK: Only SELECT queries are authorized on this view."

    for forbidden in ["DROP", "INSERT", "UPDATE", "DELETE", "ALTER", "TRUNCATE"]:
        if re.search(rf"\b{forbidden}\b", upper_query):
            return f"SECURITY BLOCK: Operation '{forbidden}' is forbidden for AI Agent."

    # Direct raw table block
    if re.search(r"\bTBL_SC_FLEET_HIST_RAW\b", upper_query):
        return "SECURITY BLOCK: Direct access to raw tables is prohibited. Query 'FDE_VIEWS.VW_ACTIVE_FLEET' instead."

    # Support T-SQL TOP N conversion to DuckDB LIMIT N
    converted_query = cleaned_query
    top_match = re.search(r"SELECT\s+TOP\s+(\d+)\s+(.*)", cleaned_query, flags=re.IGNORECASE | re.DOTALL)
    if top_match:
        limit_num = top_match.group(1)
        rest_of_query = top_match.group(2)
        converted_query = f"SELECT {rest_of_query} LIMIT {limit_num}"

    try:
        with duckdb.connect(str(DB_PATH), read_only=True) as conn:
            df = conn.execute(converted_query).df()
            
            if df.empty:
                return "No records matched the query criteria."
            
            # Format output neatly
            columns = list(df.columns)
            formatted_output = f"COLUMNS: {', '.join(columns)}\n"
            for _, row in df.head(10).iterrows():
                formatted_output += str(tuple(row.values)) + "\n"
                
            return formatted_output
    except Exception as e:
        return f"Database Error: {str(e)}"


@tool
def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
    """
    Fetches real-time weather and corridor conditions from a live REST API for given GPS coordinates.
    Provides temperature, wind speed, and computed corridor congestion index.
    """
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"
        response = requests.get(url, timeout=6)
        response.raise_for_status()
        
        payload = response.json().get("current_weather", {})
        temp = payload.get("temperature", "N/A")
        wind = payload.get("windspeed", 0.0)
        
        congestion_index = 8.5 if wind > 10.0 else 2.5
        status_note = "High Transit Disruption" if wind > 10.0 else "Corridor Normal"
        
        return (
            f"--- LIVE CORRIDOR TELEMETRY ---\n"
            f"Target GPS: {latitude}, {longitude}\n"
            f"External Temp: {temp} C | Wind Speed: {wind} km/h\n"
            f"Corridor Risk: {status_note} (Congestion Index: {congestion_index}/10)\n"
            f"-------------------------------"
        )
    except Exception as e:
        return f"Corridor API Communication Failure: {str(e)}"


@tool
def search_compliance_sop(query: str) -> str:
    """
    Searches enterprise Standard Operating Procedures (SOPs) indexed in the ChromaDB Vector DB.
    Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.
    """
    try:
        matched_docs = retriever.invoke(query)
        if not matched_docs:
            return "No matching compliance clauses found."
            
        formatted_context = "\n\n".join(
            [f"[Source: {doc.metadata.get('source_file', 'SOP')} | Format: {doc.metadata.get('file_format', 'RAW')}]\n{doc.page_content}" for doc in matched_docs]
        )
        return f"--- COMPLIANCE SOP CONTEXT ---\n{formatted_context}\n------------------------------"
    except Exception as e:
        return f"Vector Store Retrieval Error: {str(e)}"


# ==========================================
# 3. LOCAL VERIFICATION
# ==========================================
if __name__ == "__main__":
    print("\n--- Testing Tool 1: SQL Telemetry View ---")
    print(query_telemetry_db.invoke("SELECT TOP 2 Latitude, Longitude, Current_Temperature_C FROM FDE_VIEWS.VW_ACTIVE_FLEET"))
    
    print("\n--- Testing Tool 2: Live Corridor API ---")
    print(fetch_corridor_conditions.invoke({"latitude": 33.77, "longitude": -118.19}))
    
    print("\n--- Testing Tool 3: ChromaDB Vector Retrieval ---")
    print(search_compliance_sop.invoke("What are the temperature rules for fresh perishables?"))