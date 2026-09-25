"""FastAPI backend server for Context Passport.

Implements Version 1 Working Memory and Learning Brain:
- Supabase Auth JWT verification with strict test environment gating
- Request-size limits (64KB payload, 16KB text) and rate limiting (60 req/min)
- Message ingestion with safe retry state machine and event_id handling
- Injected memory blocks rejected before storage
- Secret credential screening and allergy/uncertain sensitivity classification
- Explanation-style preference promotion (>= 3 obs across >= 2 chats)
- Scoped Atlas vector memory search with fail-closed metadata checks
"""

from __future__ import annotations

import collections
import hashlib
import logging
import os
import time
from importlib.metadata import version
from typing import Any, Dict, List, Optional, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from auth import AuthenticatedUser, get_current_user, acquire_synthetic_user_token
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine, is_extension_injected_context
from security import classify_text, contains_secret

logger = logging.getLogger(__name__)

BUILD_MARKER = os.getenv("BUILD_MARKER", "context-passport-v3-api-001")
MAX_REQUEST_BYTES = 65_536  # 64 KB limit
RATE_LIMIT_WINDOW = 60.0    # 60-second window
RATE_LIMIT_MAX_REQUESTS = 60 # 60 requests per minute

app = FastAPI(
    title="Context Passport API",
    description="Privacy-first browser memory backend and self-improvement engine.",
    version="3.0.0",
)

# Enable CORS for browser extension and localhost testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiter state: key -> deque of timestamps
_rate_limit_records: dict[str, collections.deque] = collections.defaultdict(collections.deque)


@app.middleware("http")
async def rate_limit_and_size_middleware(request: Request, call_next):
    # 1. Request size limit
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": f"Payload too large. Maximum allowed size is {MAX_REQUEST_BYTES} bytes."},
                )
        except ValueError:
            pass

    # 2. Rate limiting (skip health/ready)
    path = request.url.path
    if path not in ("/health", "/ready"):
        client_ip = request.client.host if request.client else "unknown"
        auth_header = request.headers.get("authorization", "")
        # Use token fingerprint or IP as bucket key
        bucket_key = auth_header[-16:] if len(auth_header) > 16 else client_ip

        now = time.time()
        timestamps = _rate_limit_records[bucket_key]
        # Purge older than window
        while timestamps and now - timestamps[0] > RATE_LIMIT_WINDOW:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
            logger.warning("Rate limit exceeded for %s", bucket_key)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests. Please slow down."},
                headers={"Retry-After": "60"},
            )
        timestamps.append(now)

    return await call_next(request)


# Module-level singletons (lazy loaded)
memory_manager: Optional[MemoryManager] = None
preference_engine: Optional[PreferenceEngine] = None


def get_extractor():
    from providers import create_extractor
    return create_extractor()


def get_embedder():
    from providers import create_embedder
    return create_embedder()


def get_validator():
    from providers import create_validator
    return create_validator()


def get_mem_manager() -> MemoryManager:
    global memory_manager
    if memory_manager is None:
        from providers import create_extractor, create_embedder, create_validator
        memory_manager = MemoryManager(
            extractor=create_extractor(),
            embedder=create_embedder(),
            validator=create_validator(),
        )
    return memory_manager


def get_pref_engine() -> PreferenceEngine:
    global preference_engine
    if preference_engine is None:
        preference_engine = PreferenceEngine()
    return preference_engine


def get_active_llm_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER")
    if explicit:
        return explicit.lower()
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "openrouter"


def require_paid_tier_confirmation() -> None:
    """Fail closed before sending conversation text or a draft to models."""
    if os.getenv("APP_ENV", "production").lower() == "test":
        return

    provider = get_active_llm_provider()
    if provider == "gemini":
        if os.getenv("GEMINI_PAID_TIER_CONFIRMED", "false").lower() != "true":
            raise HTTPException(
                status_code=503,
                detail="Gemini paid-tier confirmation is required before capture or recall.",
            )
    elif provider == "openrouter":
        if not os.getenv("OPENROUTER_API_KEY"):
            raise HTTPException(
                status_code=503,
                detail="OPENROUTER_API_KEY is required before capture or recall.",
            )
        from providers import global_usage_tracker
        try:
            global_usage_tracker.check_cap()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))


require_provider_ready = require_paid_tier_confirmation


# Request / Response Schemas
class MessageIngestRequest(BaseModel):
    conversation_id: str = Field(..., description="Unique conversation ID on the client site")
    text: str = Field(..., max_length=16000, description="Completed user message text (max 16KB)")
    role: str = Field(default="user", description="Message author role: must be 'user'")
    is_extension_context: bool = Field(default=False, description="True if text is extension-inserted memory")
    event_id: Optional[str] = Field(default=None, description="Optional idempotency key / event ID")
    source_site: Optional[Literal["chatgpt.com", "claude.ai", "gemini.google.com", "manual"]] = None


class MemoryQueryRequest(BaseModel):
    query: str = Field(..., max_length=16000, description="Current draft or prompt text")
    max_general: int = Field(default=3, ge=1, le=10, description="Max general memories to return")


class PreferenceUpdateRequest(BaseModel):
    preference_text: str = Field(..., max_length=1000, description="User-corrected preference wording")


class MemoryUpdateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=16000, description="User-corrected memory wording")


class AuthTokenRequest(BaseModel):
    email: str
    password: str


# Endpoints
@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "context-passport",
        "version": "3.0.0",
        "build": BUILD_MARKER,
        "mem0": version("mem0ai"),
        "fastapi": version("fastapi"),
    }


@app.get("/ready")
async def ready() -> JSONResponse:
    provider = get_active_llm_provider()
    required = [
        "MONGODB_URI",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ]
    if provider == "gemini":
        required.insert(0, "GEMINI_API_KEY")
    elif provider == "openrouter":
        required.insert(0, "OPENROUTER_API_KEY")

    missing = [name for name in required if not os.getenv(name)]
    if provider == "gemini":
        if os.getenv("APP_ENV", "production").lower() != "test" and os.getenv(
            "GEMINI_PAID_TIER_CONFIRMED", "false"
        ).lower() != "true":
            missing.append("GEMINI_PAID_TIER_CONFIRMED")

    if os.getenv("ENABLE_JEV_VALIDATION", "false").lower() == "true":
        if not os.getenv("JEV_API_KEY"):
            missing.append("JEV_API_KEY")

    return JSONResponse(
        {"status": "ready" if not missing else "configuration_required", "missing": missing},
        status_code=200 if not missing else 503,
    )


@app.post("/api/v1/auth/token")
async def login_for_token(req: AuthTokenRequest) -> dict[str, Any]:
    """Helper to acquire a Supabase access token for testing or extension sign-in."""
    token = acquire_synthetic_user_token(req.email, req.password)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to authenticate credentials with Supabase",
        )
    return {"access_token": token, "token_type": "bearer"}


@app.post("/api/v1/messages/ingest")
async def ingest_message(
    req: MessageIngestRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, Any]:
    """
    Ingests a user message:
    - Verifies user identity via Supabase JWT (strict test gate)
    - Rejects non-user messages and extension injected memory blocks BEFORE saving
    - Enforces safe retry state machine with event_id (never permanently blocks failed saves)
    - Screens recognizable credentials (secrets skipped completely)
    - Records explanation style observations & evaluates promotion (>=3 obs across >=2 chats)
    - Extracts durable facts and saves to scoped memory store
    """
    if req.role != "user":
        return {"status": "skipped", "reason": "assistant_reply_ignored"}

    clean_text = req.text.strip()
    if not clean_text:
        return {"status": "skipped", "reason": "empty_text"}

    # Defense: Reject injected memory blocks before saving or processing
    if req.is_extension_context or is_extension_injected_context(clean_text):
        logger.info("Rejected message containing extension injected memory context")
        return {
            "status": "skipped",
            "reason": "extension_context_ignored",
            "message": "Injected memory blocks cannot be captured as fresh evidence",
        }

    # Secret credential screening
    if contains_secret(clean_text):
        logger.info("User %s message contained recognizable secrets; screened completely", user.user_id)
        return {
            "status": "skipped",
            "reason": "secret_credential_screened",
            "message": "Recognizable credentials or secrets detected and skipped completely",
        }

    # API connectivity cannot prove Google's billing/data-handling tier. The
    # operator must verify billing before production conversation ingestion.
    require_paid_tier_confirmation()

    message_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

    # Process explanation preference evidence with safe retry state machine
    pref_result = engine.process_message(
        user_id=user.user_id,
        conversation_id=req.conversation_id,
        text=clean_text,
        role=req.role,
        is_extension_context=req.is_extension_context,
        event_id=req.event_id,
    )

    if pref_result.get("status") == "duplicate_skipped":
        return {
            "status": "duplicate_skipped",
            "message_hash": message_hash,
            "event_id": req.event_id,
            "reason": pref_result.get("reason"),
        }

    # A retry after memory_saved must not call Mem0 a second time.
    if pref_result.get("status") == "memory_saved":
        facts = pref_result.get("facts_extracted", [])
    else:
        try:
            fact_result = mem_mgr.add(
                text=clean_text,
                user_id=user.user_id,
                metadata={
                    "conversation_id": req.conversation_id,
                    "source_event_key": engine._event_key(user.user_id, message_hash, req.event_id),
                    "source_site": req.source_site,
                },
            )
        except Exception as exc:
            logger.error("Memory save failed for user %s (%s)", user.user_id, type(exc).__name__)
            engine.mark_event_failed(user.user_id, message_hash, type(exc).__name__, event_id=req.event_id)
            raise HTTPException(status_code=500, detail="Memory save failed; retry this event.") from exc
        if fact_result.get("reason") == "forgotten_source":
            engine.mark_memory_saved(user.user_id, message_hash, [], event_id=req.event_id)
            engine.mark_event_completed(user.user_id, message_hash, event_id=req.event_id)
            return {"status": "skipped", "reason": "forgotten_source"}
        facts = fact_result.get("results", [])
        engine.mark_memory_saved(user.user_id, message_hash, facts, event_id=req.event_id)

    try:
        pref_result = engine.complete_message(
            user.user_id, req.conversation_id, clean_text, message_hash, event_id=req.event_id,
        )
    except Exception as exc:
        logger.error("Learning evidence commit failed for user %s (%s)", user.user_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Memory saved; learning evidence pending retry.") from exc

    return {
        "status": "processed",
        "facts_extracted": facts,
        "observations_recorded": pref_result.get("observations_recorded", []),
        "preference_promoted": pref_result.get("preference_promoted", False),
        "promoted_preferences": pref_result.get("promoted_preferences", []),
    }


@app.post("/api/v1/memories/query")
async def query_memories(
    req: MemoryQueryRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, Any]:
    """
    Queries memories for prompt preparation:
    - Scoped strictly to authenticated user
    - Automatically returns up to max_general relevant general memories
    - Returns sensitive candidates separately (requiring explicit 'Allow once' approval)
    - Returns active promoted explanation preferences
    """
    require_paid_tier_confirmation()
    search_res = mem_mgr.search(
        query=req.query,
        user_id=user.user_id,
        max_general=req.max_general,
    )

    # Active preferences
    preferences = engine.get_user_preferences(user_id=user.user_id, status_filter="active")

    return {
        "general_memories": search_res.get("general_memories", []),
        "sensitive_memories": search_res.get("sensitive_memories", []),
        "preferences": preferences,
    }


@app.get("/api/v1/preferences")
async def get_preferences(
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> list[dict[str, Any]]:
    return engine.get_user_preferences(user_id=user.user_id, status_filter="")


@app.put("/api/v1/preferences/{preference_id}")
async def update_preference(
    preference_id: str,
    req: PreferenceUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, Any]:
    try:
        return engine.update_preference_text(
            user_id=user.user_id,
            preference_id=preference_id,
            new_text=req.preference_text,
            lock=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400 if "wording" in str(exc) else 404, detail=str(exc))


@app.delete("/api/v1/preferences/{preference_id}/observations/{observation_id}")
async def remove_preference_evidence(
    preference_id: str,
    observation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, bool]:
    pref = engine.preferences_col.find_one({"_id": preference_id, "user_id": user.user_id})
    if not pref:
        raise HTTPException(status_code=404, detail="Preference not found")
    observation = engine.observations_col.find_one({"_id": observation_id, "user_id": user.user_id})
    if not observation or observation.get("preference_key") != pref.get("preference_key"):
        raise HTTPException(status_code=404, detail="Evidence not found")
    return {"ok": engine.remove_observation(user.user_id, observation_id)}


@app.post("/api/v1/preferences/{preference_id}/block")
async def block_preference(
    preference_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, bool]:
    if not engine.block_preference(user.user_id, preference_id):
        raise HTTPException(status_code=404, detail="Preference not found or already blocked")
    return {"ok": True}


@app.delete("/api/v1/preferences/{preference_id}")
async def forget_preference(
    preference_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    engine: PreferenceEngine = Depends(get_pref_engine),
) -> dict[str, bool]:
    if not engine.forget_preference(user.user_id, preference_id):
        raise HTTPException(status_code=404, detail="Preference not found")
    return {"ok": True}


@app.get("/api/v1/memories")
async def get_all_memories(
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> list[dict[str, Any]]:
    return mem_mgr.get_all(user_id=user.user_id)


@app.put("/api/v1/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    req: MemoryUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, Any]:
    try:
        return mem_mgr.update(memory_id=memory_id, user_id=user.user_id, text=req.text)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Memory not found for user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/v1/memories/{memory_id}/block")
async def block_memory(
    memory_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, bool]:
    try:
        ok = mem_mgr.block(memory_id=memory_id, user_id=user.user_id)
        return {"ok": ok}
    except PermissionError:
        raise HTTPException(status_code=404, detail="Memory not found for user")


@app.delete("/api/v1/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mem_mgr: MemoryManager = Depends(get_mem_manager),
) -> dict[str, bool]:
    try:
        ok = mem_mgr.delete(memory_id=memory_id, user_id=user.user_id)
        return {"ok": ok}
    except PermissionError:
        raise HTTPException(status_code=404, detail="Memory not found for user")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
