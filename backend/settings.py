from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_extraction_model: str
    gemini_embedding_model: str
    gemini_embedding_dims: int
    mongodb_uri: str
    mongodb_db_name: str
    mongodb_collection_name: str
    mongodb_vector_index_name: str
    history_db_path: str


def load_settings(require_gemini: bool = True) -> Settings:
    history_path = Path(os.getenv("MEM0_HISTORY_DB_PATH", "backend/mem0_history.db"))
    if not history_path.is_absolute():
        history_path = ROOT_DIR / history_path
    gemini_key = _required("GEMINI_API_KEY") if require_gemini else os.getenv("GEMINI_API_KEY", "")
    return Settings(
        gemini_api_key=gemini_key,
        gemini_extraction_model=os.getenv("GEMINI_EXTRACTION_MODEL", "gemini-2.5-flash"),
        gemini_embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001"),
        gemini_embedding_dims=int(os.getenv("GEMINI_EMBEDDING_DIMS", "1536")),
        mongodb_uri=_required("MONGODB_URI"),
        mongodb_db_name=os.getenv("MONGODB_DB_NAME", "context_passport"),
        mongodb_collection_name=os.getenv("MONGODB_COLLECTION_NAME", "memories"),
        mongodb_vector_index_name=os.getenv("MONGODB_VECTOR_INDEX_NAME", "memories_vector_index_scoped"),
        history_db_path=str(history_path),
    )
