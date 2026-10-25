import os
import hashlib
import json
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import pypdf

from langchain_chroma import Chroma
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from langchain_core.documents import Document

# Suppress the Windows symlink warning noise completely
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"

# ==========================================
# 1. PATH RESOLUTION & SETUP
# ==========================================
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
load_dotenv(project_root / ".env")

# Set up hash cache path (tracks which files were already ingested)
cache_dir = project_root / "data" / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)
HASH_CACHE_FILE = cache_dir / "ingestion_hash_cache.json"

hash_cache = {}
if HASH_CACHE_FILE.exists():
    try:
        with open(HASH_CACHE_FILE, "r") as f:
            hash_cache = json.load(f)
    except Exception:
        hash_cache = {}

# ==========================================
# 2. DYNAMIC ENVIRONMENT ROUTING
#    Set Embeddings_model=OPENAI in .env for cloud,
#    or leave blank/LOCAL for free HuggingFace model.
# ==========================================
EMBEDDINGS_MODEL_SETTING = os.getenv("Embeddings_model", "LOCAL").strip().upper()

if EMBEDDINGS_MODEL_SETTING == "OPENAI":
    from langchain_openai import OpenAIEmbeddings

    print("🤖 Mode: Cloud OpenAI Embeddings (1536 Dim)...")
    embeddings = OpenAIEmbeddings()
    COLLECTION_NAME = "fde-sop-openai"  # separate collection per model type
else:
    from langchain_huggingface import HuggingFaceEmbeddings

    local_model_target = os.getenv("Local_Embedding_Model", "BAAI/bge-m3").strip()
    print(f"🤗 Mode: Local HuggingFace [{local_model_target}]...")
    embeddings = HuggingFaceEmbeddings(
        model_name=local_model_target,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    COLLECTION_NAME = "fde-sop-local"  # separate collection per model type

# ==========================================
# 3. CHROMADB SETUP
#    No API key needed. Data is stored locally
#    in:  data/chroma_db/  (auto-created)
# ==========================================
CHROMA_DB_DIR = project_root / "data" / "chroma_db"
CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)

print(f"💾 ChromaDB path  : [{CHROMA_DB_DIR}]")
print(f"📂 Collection name: [{COLLECTION_NAME}]")

vector_store = Chroma(
    collection_name=COLLECTION_NAME,
    embedding_function=embeddings,
    persist_directory=str(CHROMA_DB_DIR),  # data survives restarts automatically
)

# Direct access to the underlying ChromaDB collection (used for delete operations)
chroma_collection = vector_store._collection


def delete_by_source(source_file: str) -> None:
    """Delete all vectors in ChromaDB that belong to a given source file."""
    try:
        # ChromaDB where-filter: find all docs with this source_file metadata
        results = chroma_collection.get(where={"source_file": source_file})
        ids_to_delete = results.get("ids", [])
        if ids_to_delete:
            chroma_collection.delete(ids=ids_to_delete)
            print(
                f"  🗑️  Removed {len(ids_to_delete)} old vector(s) for [{source_file}]"
            )
    except Exception as e:
        print(f"  ⚠️  Could not delete old vectors for [{source_file}]: {e}")


# ==========================================
# 4. ROBUST POLYMORPHIC PARSER
#    Supports: .md  .txt  .pdf  .csv  .xlsx
# ==========================================
def parse_and_chunk_document(doc_path: Path) -> list[Document]:
    """Parse any supported file type into a list of LangChain Document chunks."""
    ext = doc_path.suffix.lower()
    raw_chunks: list[Document] = []

    # Each chunk ≤ 600 chars with 60-char overlap to preserve context at boundaries
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=60)

    if ext == ".md":
        # Split by Markdown headers first, then by character count
        headers_to_split_on = [
            ("#", "Header_1"),
            ("##", "Header_2"),
            ("###", "Header_3"),
        ]
        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on
        )
        raw_text = doc_path.read_text(encoding="utf-8")
        header_docs = md_splitter.split_text(raw_text)
        raw_chunks = text_splitter.split_documents(header_docs)

    elif ext == ".txt":
        raw_text = doc_path.read_text(encoding="utf-8")
        raw_docs = [Document(page_content=raw_text)]
        raw_chunks = text_splitter.split_documents(raw_docs)

    elif ext == ".pdf":
        pdf_docs = []
        try:
            with open(doc_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                for page_num, page in enumerate(reader.pages):
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        pdf_docs.append(
                            Document(
                                page_content=page_text,
                                metadata={"page_number": page_num + 1},
                            )
                        )
            raw_chunks = text_splitter.split_documents(pdf_docs)
        except Exception as e:
            print(f"  ❌ Error parsing PDF {doc_path.name}: {e}")
            return []

    elif ext in [".csv", ".xlsx"]:
        try:
            df = pd.read_csv(doc_path) if ext == ".csv" else pd.read_excel(doc_path)
        except Exception as e:
            print(f"  ❌ Error reading table {doc_path.name}: {e}")
            return []

        # Each row becomes its own Document
        for idx, row in df.iterrows():
            row_dict = row.to_dict()
            row_items = [
                f"{str(col)}: {str(val)}"
                for col, val in row_dict.items()
                if pd.notna(val) and str(val).strip() != ""
            ]
            if row_items:
                row_text = " | ".join(row_items)
                raw_chunks.append(
                    Document(page_content=row_text, metadata={"row_index": int(idx)})
                )

    # Filter out empty/whitespace-only chunks
    valid_chunks = []
    for chunk in raw_chunks:
        clean_text = chunk.page_content.strip()
        if clean_text:
            chunk.page_content = clean_text
            valid_chunks.append(chunk)

    return valid_chunks


# ==========================================
# 5. INCREMENTAL PIPELINE WITH BATCHING
#    Only processes new or changed files.
#    Removes vectors for deleted files.
# ==========================================
policy_dir = project_root / "data" / "policy"
target_patterns = ["*.md", "*.txt", "*.pdf", "*.csv", "*.xlsx"]
current_files = {}

for pattern in target_patterns:
    for file_path in policy_dir.glob(pattern):
        current_files[file_path.name] = file_path

print(f"📂 Found {len(current_files)} policy file(s) in [{policy_dir}]")

updated_cache = {}
cache_modified = False

cached_filenames = set(hash_cache.keys())
current_filenames = set(current_files.keys())
deleted_files = cached_filenames - current_filenames

# --- Remove vectors for files that no longer exist on disk ---
for deleted_file in deleted_files:
    print(f"🗑️  Detected deleted file: [{deleted_file}]. Purging from ChromaDB...")
    delete_by_source(deleted_file)
    cache_modified = True

# --- Process new or changed files ---
for file_name, file_path in current_files.items():
    file_bytes = file_path.read_bytes()
    file_hash = hashlib.md5(file_bytes).hexdigest()
    updated_cache[file_name] = file_hash

    # Skip if file hasn't changed since last run
    if hash_cache.get(file_name) == file_hash:
        print(f"✨ Skipped (Unchanged): {file_name}")
        continue

    print(f"🔄 Processing: {file_name}...")
    cache_modified = True

    try:
        # Delete old vectors for this file before re-uploading
        delete_by_source(file_name)

        chunks = parse_and_chunk_document(file_path)
        if not chunks:
            print(f"  ⚠️  No valid text chunks extracted from {file_name}.")
            continue

        # Attach metadata to every chunk
        explicit_ids = []
        for idx, chunk in enumerate(chunks):
            chunk.metadata["source_file"] = file_name
            chunk.metadata["file_format"] = file_path.suffix.replace(".", "").upper()
            chunk.metadata["document_type"] = "Compliance Asset"
            explicit_ids.append(f"{file_name}-chunk-{idx}")

        # Upload in batches of 100 (ChromaDB handles this well locally)
        batch_size = 100
        total_chunks = len(chunks)
        print(f"  📤 Adding {total_chunks} chunk(s) in batches of {batch_size}...")

        for i in range(0, total_chunks, batch_size):
            batch_docs = chunks[i : i + batch_size]
            batch_ids = explicit_ids[i : i + batch_size]
            vector_store.add_documents(documents=batch_docs, ids=batch_ids)

        print(f"  ✅ Done: {file_name} ({total_chunks} chunks stored)")

    except Exception as e:
        print(f"❌ Error during ingestion of {file_name}: {e}")
        updated_cache.pop(file_name, None)  # Don't cache failed files

# ==========================================
# 6. SYNC HASH CACHE
# ==========================================
if cache_modified:
    with open(HASH_CACHE_FILE, "w") as f:
        json.dump(updated_cache, f, indent=4)
    print("\n✅ Ingestion & cache sync complete.")
else:
    print("\n🌴 ChromaDB is already up-to-date. Nothing to do.")
