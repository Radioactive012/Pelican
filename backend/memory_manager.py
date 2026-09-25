"""Memory manager and tenant-safe facade around Mem0 OSS and MongoDB Atlas."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# Mem0 telemetry creates an extra Atlas vector collection/index and sends
# lifecycle events. Neither is needed for this privacy-first prototype.
os.environ.setdefault("MEM0_TELEMETRY", "false")

from mem0 import Memory
from mem0.utils.factory import VectorStoreFactory
from pymongo import MongoClient

from security import classify_text, contains_secret, redacted_evidence_excerpt
from settings import Settings, load_settings

logger = logging.getLogger(__name__)
MIN_MEMORY_SCORE = float(os.getenv("MIN_MEMORY_SCORE", "0.78"))


def generate_deterministic_embedding(text: str, dims: int = 1536) -> List[float]:
    """Generates a deterministic unit-normalized 1536-dimensional vector for offline test suites only."""
    clean_text = text.lower().strip()
    words = re.findall(r"\b\w+\b", clean_text)
    vector = [0.0] * dims
    stopwords = {
        "is", "my", "the", "a", "an", "and", "or", "in", "on", "at", "to",
        "for", "of", "with", "it", "this", "can", "you", "please", "what",
        "which", "how", "do", "i", "me", "are", "be", "so", "that"
    }

    for word in words:
        weight = 0.1 if word in stopwords else 1.0
        h = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
        for seed in range(5):
            idx = (h >> (seed * 8)) % dims
            vector[idx] += weight * (1.0 / (seed + 1))

        if len(word) >= 3 and word not in stopwords:
            for i in range(len(word) - 2):
                ngram = word[i : i + 3]
                nh = int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16)
                idx = nh % dims
                vector[idx] += 0.3

    norm = math.sqrt(sum(x * x for x in vector))
    if norm > 0:
        return [x / norm for x in vector]
    vector[0] = 1.0
    return vector


def build_mem0_config(settings: Settings) -> dict[str, Any]:
    config: dict[str, Any] = {
        "version": "v1.1",
        "history_db_path": settings.history_db_path,
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

    if settings.gemini_api_key:
        config["llm"] = {
            "provider": "gemini",
            "config": {
                "api_key": settings.gemini_api_key,
                "model": settings.gemini_extraction_model,
                "temperature": 0,
            },
        }
        config["embedder"] = {
            "provider": "gemini",
            "config": {
                "api_key": settings.gemini_api_key,
                "model": settings.gemini_embedding_model,
                "embedding_dims": settings.gemini_embedding_dims,
            },
        }
    return config


class MemoryManager:
    """Tenant-safe facade around Mem0 OSS and MongoDB Atlas vector storage."""

    def __init__(self, settings: Settings | None = None):
        allow_offline = os.getenv("ALLOW_OFFLINE_EMBEDDINGS", "false").lower() == "true"
        self._allow_offline = allow_offline
        self.settings = settings or load_settings(require_gemini=not allow_offline)
        VectorStoreFactory.provider_to_class["mongodb"] = "scoped_mongodb.ScopedMongoDB"
        self.mongo_client = MongoClient(self.settings.mongodb_uri)
        self.db = self.mongo_client[self.settings.mongodb_db_name]
        self.collection = self.db[self.settings.mongodb_collection_name]
        self.forgotten_sources = self.db["forgotten_memory_sources"]

        # Require Gemini in real V1 mode
        if not self.settings.gemini_api_key and not self._allow_offline:
            raise RuntimeError(
                "GEMINI_API_KEY is required in real V1 mode. Silent fallback embeddings are disabled."
            )

        if self.settings.gemini_api_key:
            self.memory = Memory.from_config(build_mem0_config(self.settings))
            # Mem0 creates SDK clients without request deadlines. Reuse one
            # bounded client so a stalled provider cannot hang capture forever.
            from google import genai
            from google.genai import types

            self._genai_client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(
                    timeout=30_000,
                    retry_options=types.HttpRetryOptions(attempts=2),
                ),
            )
            self.memory.llm.client = self._genai_client
            self.memory.embedding_model.client = self._genai_client
        else:
            self.memory = None

    def embed_text(self, text: str) -> List[float]:
        """
        Generates 1536-dimensional embedding using real Gemini API.
        Never silently uses fallback embeddings in real V1 mode.
        """
        if self.settings.gemini_api_key:
            from google.genai import types

            config = types.EmbedContentConfig(output_dimensionality=self.settings.gemini_embedding_dims)
            resp = self._genai_client.models.embed_content(
                model=self.settings.gemini_embedding_model,
                contents=text,
                config=config,
            )
            if resp.embeddings and resp.embeddings[0].values:
                return list(resp.embeddings[0].values)
            raise RuntimeError("Gemini embed_content returned no embeddings")

        if self._allow_offline:
            return generate_deterministic_embedding(text, self.settings.gemini_embedding_dims)

        raise RuntimeError("GEMINI_API_KEY is required for embeddings in real V1 mode.")

    def add(
        self,
        text: str,
        user_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Adds a memory after screening for secrets and assigning classification.
        - Extension injected memory blocks are REJECTED before saving.
        - Secrets are SKIPPED completely.
        - General facts tagged 'general'.
        - Sensitive/uncertain/allergy facts tagged 'sensitive'.
        """
        # Reject extension injected context blocks before saving
        from preference_engine import is_extension_injected_context
        if is_extension_injected_context(text):
            logger.info("MemoryManager.add rejected extension injected memory block")
            return {
                "status": "skipped",
                "reason": "extension_context_ignored",
                "results": [],
            }

        classification = classify_text(text)
        if classification == "secret":
            logger.info("Skipped memory creation: recognizable secret credential screened")
            return {
                "status": "skipped",
                "reason": "secret_credential_screened",
                "results": [],
            }

        # A provider/database failure may occur after Mem0 writes but before the
        # dedup event is marked memory_saved. On retry, reuse that event's facts.
        source_event_key = (metadata or {}).get("source_event_key")
        if source_event_key and self._source_forgotten(user_id, source_event_key):
            return {"status": "skipped", "reason": "forgotten_source", "results": []}
        if source_event_key:
            prior = list(self.collection.find({
                "payload.user_id": user_id,
                "payload.source_event_key": source_event_key,
            }))
            if prior:
                return {"results": [
                    {
                        "id": str(doc["_id"]),
                        "memory": doc.get("payload", {}).get("data") or doc.get("text", ""),
                        "classification": doc.get("payload", {}).get("classification", "sensitive"),
                        "event": "EXISTING",
                    }
                    for doc in prior
                ]}

        # If Mem0 with Gemini is fully available and no custom offline override
        if self.memory and self.settings.gemini_api_key:
            mem_meta = {
                "classification": classification,
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                **(metadata or {}),
            }
            res = self.memory.add(text, user_id=user_id, metadata=mem_meta)
            # Ensure classification, memory text, and status are stored in collection doc
            for item in res.get("results", []):
                m_id = item.get("id")
                if m_id:
                    self.collection.update_one(
                        {"_id": m_id},
                        {
                            "$set": {
                                "payload.classification": classification,
                                "payload.status": "active",
                                "payload.data": item.get("memory", text),
                                "payload.support_excerpt": redacted_evidence_excerpt(
                                    item.get("memory", text), sensitive=classification == "sensitive"
                                ),
                            }
                        },
                    )
                item["classification"] = classification
            if source_event_key and self._source_forgotten(user_id, source_event_key):
                self._delete_source_memories(user_id, source_event_key)
                return {"status": "skipped", "reason": "forgotten_source", "results": []}
            return res

        # Standalone mode (only when explicit offline mode is enabled)
        if not self._allow_offline:
            raise RuntimeError("Gemini memory extraction is required in real V1 mode.")

        doc_id = str(uuid.uuid4())
        embedding = self.embed_text(text)
        now_iso = datetime.now(timezone.utc).isoformat()
        doc = {
            "_id": doc_id,
            "text": text,
            "embedding": embedding,
            "payload": {
                "user_id": user_id,
                "data": text,
                "classification": classification,
                "status": "active",
                "created_at": now_iso,
                "support_excerpt": redacted_evidence_excerpt(text, sensitive=classification == "sensitive"),
                **(metadata or {}),
            },
        }
        self.collection.insert_one(doc)
        if source_event_key and self._source_forgotten(user_id, source_event_key):
            self._delete_source_memories(user_id, source_event_key)
            return {"status": "skipped", "reason": "forgotten_source", "results": []}
        return {
            "results": [
                {
                    "id": doc_id,
                    "memory": text,
                    "event": "ADD",
                    "classification": classification,
                }
            ]
        }

    def search(
        self,
        query: str,
        user_id: str,
        *,
        top_k: int = 10,
        max_general: int = 3,
    ) -> Dict[str, Any]:
        """
        Searches user memories using Atlas vector search pre-filtered on payload.user_id.
        FAILS CLOSED when classification or control metadata is missing or invalid.
        Returns:
        - 'general_memories': up to max_general relevant memories
        - 'sensitive_memories': sensitive candidates requiring approval
        """
        query_vector = self.embed_text(query)
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.settings.mongodb_vector_index_name,
                    "path": "embedding",
                    "queryVector": query_vector,
                    "numCandidates": min(max(top_k * 20, 100), 10000),
                    "limit": top_k,
                    "filter": {"payload.user_id": {"$eq": user_id}},
                }
            },
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"embedding": 0}},
        ]

        try:
            candidates = list(self.collection.aggregate(pipeline))
        except Exception as exc:
            logger.warning("Atlas vector search notice (%s)", exc)
            candidates = []

        # Atlas indexes new writes asynchronously. Even when older candidates
        # exist, include direct-scored recent documents so a fresh fact is not
        # invisible during the first cross-site recall.
        direct_query: Dict[str, Any] = {"payload.user_id": user_id, "payload.status": "active"}
        if candidates:
            direct_query["payload.created_at"] = {
                "$gte": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            }
        user_docs = list(self.collection.find(direct_query))
        combined = {str(doc["_id"]): doc for doc in candidates}
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        for doc in user_docs:
            doc_emb = doc.get("embedding", [])
            if not doc_emb or len(doc_emb) != len(query_vector) or not query_norm:
                continue
            doc_norm = math.sqrt(sum(value * value for value in doc_emb))
            if not doc_norm:
                continue
            cosine = sum(a * b for a, b in zip(query_vector, doc_emb)) / (query_norm * doc_norm)
            # Atlas cosine vectorSearchScore maps cosine [-1, 1] to [0, 1].
            doc["score"] = (1.0 + cosine) / 2.0
            key = str(doc["_id"])
            if key not in combined or doc["score"] > combined[key].get("score", 0):
                combined[key] = doc
        candidates = sorted(combined.values(), key=lambda d: d.get("score", 0), reverse=True)[:top_k]

        general_memories: List[Dict[str, Any]] = []
        sensitive_memories: List[Dict[str, Any]] = []

        for doc in candidates:
            # Atlas cosine scores can be high even for unrelated prompts. Do not
            # send a weak match to another assistant merely because it is top-k.
            score = doc.get("score")
            if not isinstance(score, (int, float)) or not math.isfinite(score) or score < MIN_MEMORY_SCORE:
                continue
            payload = doc.get("payload")
            # FAIL CLOSED: Missing or malformed payload
            if not payload or not isinstance(payload, dict):
                continue

            # FAIL CLOSED: Mismatched ownership
            if payload.get("user_id") != user_id:
                continue

            # FAIL CLOSED: Status must be strictly 'active' (absent, blocked, or deleted fails closed)
            if payload.get("status") != "active":
                continue

            text_content = payload.get("data") or doc.get("text", "")
            if not text_content:
                continue

            # Defense-in-depth: Secret credentials in text are completely screened
            if contains_secret(text_content):
                continue

            # A missing policy label is not approval to expose memory text.
            raw_classification = payload.get("classification")
            if raw_classification not in ("general", "sensitive"):
                continue
            classification = raw_classification

            # If text indicates allergy or uncertain, enforce sensitive
            if classify_text(text_content) == "sensitive":
                classification = "sensitive"

            memory_obj = {
                "id": str(doc["_id"]),
                "text": text_content,
                "memory": text_content,
                "score": doc.get("score"),
                "classification": classification,
                "created_at": payload.get("created_at"),
            }

            if classification == "sensitive":
                sensitive_memories.append(memory_obj)
            elif classification == "general":
                if len(general_memories) < max_general:
                    general_memories.append(memory_obj)
            # Anything else fails closed

        return {
            "results": general_memories + sensitive_memories,
            "general_memories": general_memories,
            "sensitive_memories": sensitive_memories,
        }

    def get_all(self, user_id: str) -> List[Dict[str, Any]]:
        """Returns all non-deleted memories for the user."""
        docs = self.collection.find({
            "payload.user_id": user_id,
            "payload.status": {"$ne": "deleted"},
        })
        results = []
        for doc in docs:
            payload = doc.get("payload", {})
            text = payload.get("data") or doc.get("text", "")
            source = payload.get("source_site") or self._source_from_conversation(payload.get("conversation_id", ""))
            results.append({
                "id": str(doc["_id"]),
                "text": text,
                "memory": text,
                "classification": payload.get("classification", "sensitive"),
                "status": payload.get("status", "active"),
                "created_at": payload.get("created_at"),
                "source": source,
                "evidence_excerpt": payload.get("support_excerpt") or redacted_evidence_excerpt(
                    text, sensitive=payload.get("classification") == "sensitive"
                ),
            })
        return results

    @staticmethod
    def _source_from_conversation(conversation_id: str) -> str:
        for prefix, site in (("chatgpt", "chatgpt.com"), ("claude", "claude.ai"), ("gemini", "gemini.google.com")):
            if conversation_id.startswith(prefix):
                return site
        return "Unknown source"

    def _source_forgotten(self, user_id: str, source_event_key: str) -> bool:
        if not hasattr(self, "forgotten_sources"):
            return False
        return bool(self.forgotten_sources.find_one({"_id": source_event_key, "user_id": user_id}))

    def _delete_source_memories(self, user_id: str, source_event_key: str) -> None:
        tombstone = self.forgotten_sources.find_one({"_id": source_event_key, "user_id": user_id}) or {}
        allowed_ids = set(tombstone.get("allowed_memory_ids", []))
        docs = list(self.collection.find({"payload.user_id": user_id, "payload.source_event_key": source_event_key}))
        for doc in docs:
            memory_id = str(doc["_id"])
            if memory_id in allowed_ids:
                continue
            if self.memory is not None:
                self.memory.delete(memory_id)
                self._purge_history(memory_id)
            else:
                self.collection.delete_one({"_id": doc["_id"], "payload.user_id": user_id})

    def _assert_owner(self, memory_id: str, user_id: str) -> Dict[str, Any]:
        if hasattr(self, "collection") and self.collection is not None:
            doc = self.collection.find_one({"_id": memory_id})
            if not doc or doc.get("payload", {}).get("user_id") != user_id:
                raise PermissionError("Memory does not exist for this user")
            return doc
        elif hasattr(self, "memory") and self.memory is not None:
            mem = self.memory.get(memory_id)
            if not mem or mem.get("user_id") != user_id:
                raise PermissionError("Memory does not exist for this user")
            return mem
        raise PermissionError("No backend store available to verify ownership")

    def update(self, memory_id: str, user_id: str, *, text: str) -> Dict[str, Any]:
        self._assert_owner(memory_id, user_id)
        text = text.strip()
        if not text:
            raise ValueError("Memory wording cannot be empty")
        classification = classify_text(text)
        if classification == "secret":
            raise ValueError("Cannot update memory to contain secrets")

        now_iso = datetime.now(timezone.utc).isoformat()
        if hasattr(self, "memory") and self.memory is not None:
            self.memory.update(memory_id, text=text)
            if hasattr(self, "collection") and self.collection is not None:
                self.collection.update_one(
                    {"_id": memory_id},
                    {
                    "$set": {
                            "payload.data": text,
                            "payload.classification": classification,
                            "payload.updated_at": now_iso,
                        }
                    },
                )
            return {"id": memory_id, "text": text, "memory": text, "classification": classification}

        if hasattr(self, "collection") and self.collection is not None:
            new_vector = self.embed_text(text)
            self.collection.update_one(
                {"_id": memory_id},
                {
                    "$set": {
                        "text": text,
                        "embedding": new_vector,
                        "payload.data": text,
                        "payload.classification": classification,
                        "payload.updated_at": now_iso,
                    }
                },
            )
        return {"id": memory_id, "text": text, "memory": text, "classification": classification}

    def block(self, memory_id: str, user_id: str) -> bool:
        """Immediately blocks a memory from active retrieval."""
        self._assert_owner(memory_id, user_id)
        if hasattr(self, "collection") and self.collection is not None:
            res = self.collection.update_one(
                {"_id": memory_id},
                {"$set": {"payload.status": "blocked", "payload.blocked_at": datetime.now(timezone.utc).isoformat()}},
            )
            return res.modified_count > 0
        return True

    def delete(self, memory_id: str, user_id: str) -> bool:
        """Deletes a memory for the user."""
        doc = self._assert_owner(memory_id, user_id)
        source_event_key = doc.get("payload", {}).get("source_event_key")
        if source_event_key and hasattr(self, "forgotten_sources"):
            siblings = [str(item["_id"]) for item in self.collection.find({
                "payload.user_id": user_id, "payload.source_event_key": source_event_key,
            }) if str(item["_id"]) != memory_id]
            self.forgotten_sources.update_one(
                {"_id": source_event_key, "user_id": user_id},
                {"$setOnInsert": {"_id": source_event_key, "user_id": user_id,
                                  "allowed_memory_ids": siblings,
                                  "created_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
            self.forgotten_sources.update_one(
                {"_id": source_event_key, "user_id": user_id},
                {"$pull": {"allowed_memory_ids": memory_id}},
            )
        if hasattr(self, "memory") and self.memory is not None:
            # Block recall first. Mem0 appends a DELETE history row but retains
            # plaintext ADD/UPDATE rows, so purge those rows after deletion.
            self.block(memory_id, user_id)
            self.memory.delete(memory_id)
            self._purge_history(memory_id)
            return True
        if hasattr(self, "collection") and self.collection is not None:
            self.collection.delete_one({"_id": memory_id})
        return True

    def _purge_history(self, memory_id: str) -> None:
        """Remove plaintext Mem0 history for one owned, deleted memory."""
        if not self.memory:
            return
        if not hasattr(self.memory, "db"):
            if isinstance(self.memory, Memory):
                raise RuntimeError("Mem0 history database is unavailable for purge")
            return  # Lightweight fakes used by ownership unit tests have no history.
        history_db = self.memory.db
        with history_db._lock:
            history_db.connection.execute("DELETE FROM history WHERE memory_id = ?", (memory_id,))
            history_db.connection.commit()
            remaining = history_db.connection.execute(
                "SELECT COUNT(*) FROM history WHERE memory_id = ?", (memory_id,)
            ).fetchone()[0]
        if remaining:
            raise RuntimeError("Memory history purge could not be verified")

    def history(self, memory_id: str, user_id: str):
        """Retrieves history for a memory after verifying tenant ownership."""
        self._assert_owner(memory_id, user_id)
        if hasattr(self, "memory") and self.memory is not None:
            return self.memory.history(memory_id)
        return []

    def delete_all(self, user_id: str):
        """Deletes all memories belonging to user_id."""
        if hasattr(self, "collection") and self.collection is not None:
            ids = [str(doc["_id"]) for doc in self.collection.find({"payload.user_id": user_id}, {"_id": 1})]
            self.collection.delete_many({"payload.user_id": user_id})
            if getattr(self, "memory", None) is not None:
                for memory_id in ids:
                    self._purge_history(memory_id)
        if hasattr(self, "memory") and self.memory is not None:
            try:
                self.memory.delete_all(user_id=user_id)
            except Exception:
                pass
        if hasattr(self, "forgotten_sources"):
            self.forgotten_sources.delete_many({"user_id": user_id})
