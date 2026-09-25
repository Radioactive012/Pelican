from __future__ import annotations

from typing import Any

from mem0 import Memory
from mem0.utils.factory import VectorStoreFactory

from settings import Settings, load_settings


def build_mem0_config(settings: Settings) -> dict[str, Any]:
    return {
        "version": "v1.1",
        "history_db_path": settings.history_db_path,
        "llm": {
            "provider": "gemini",
            "config": {
                "api_key": settings.gemini_api_key,
                "model": settings.gemini_extraction_model,
                "temperature": 0,
            },
        },
        "embedder": {
            "provider": "gemini",
            "config": {
                "api_key": settings.gemini_api_key,
                "model": settings.gemini_embedding_model,
                "embedding_dims": settings.gemini_embedding_dims,
            },
        },
        "vector_store": {
            "provider": "mongodb",
            "config": {
                "mongo_uri": settings.mongodb_uri,
                "db_name": settings.mongodb_db_name,
                "collection_name": settings.mongodb_collection_name,
                "embedding_model_dims": settings.gemini_embedding_dims,
            },
        },
    }


class MemoryManager:
    """Tenant-safe facade around the Mem0 API surface used by the product."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        VectorStoreFactory.provider_to_class["mongodb"] = "scoped_mongodb.ScopedMongoDB"
        self.memory = Memory.from_config(build_mem0_config(self.settings))

    def add(self, messages: Any, user_id: str):
        return self.memory.add(messages, user_id=user_id)

    def search(self, query: str, user_id: str, *, top_k: int = 10):
        return self.memory.search(query, filters={"user_id": user_id}, top_k=top_k)

    def get_all(self, user_id: str):
        return self.memory.get_all(filters={"user_id": user_id})

    def _assert_owner(self, memory_id: str, user_id: str) -> dict[str, Any]:
        memory = self.memory.get(memory_id)
        if not memory or memory.get("user_id") != user_id:
            raise PermissionError("Memory does not exist for this user")
        return memory

    def update(self, memory_id: str, user_id: str, *, text: str):
        self._assert_owner(memory_id, user_id)
        return self.memory.update(memory_id, text=text)

    def delete(self, memory_id: str, user_id: str):
        self._assert_owner(memory_id, user_id)
        return self.memory.delete(memory_id)

    def history(self, memory_id: str, user_id: str):
        self._assert_owner(memory_id, user_id)
        return self.memory.history(memory_id)

    def delete_all(self, user_id: str):
        return self.memory.delete_all(user_id=user_id)
