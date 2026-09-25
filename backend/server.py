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
from contextlib import asynccontextmanager
from importlib.metadata import version
import threading
from typing import Any, Dict, List, Optional, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from auth import AuthenticatedUser, get_current_user, acquire_synthetic_user_token
from memory_manager import MemoryManager
from preference_engine import PreferenceEngine, is_extension_injected_context
from providers import (
    ProviderError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    SpendingCapExceededError,
    ProviderOutageError,
    InvalidProviderResponseError,
    MissingConfigurationError,
)
from security import classify_text, contains_secret

logger = logging.getLogger(__name__)

BUILD_MARKER = os.getenv("BUILD_MARKER", "context-passport-v3-api-001")
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "65536"))  # 64 KB limit
RATE_LIMIT_WINDOW = float(os.getenv("RATE_LIMIT_WINDOW", "60.0"))    # 60-second window
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "60")) # 60 requests per minute

# Module-level singletons (lazy loaded)
memory_manager: Optional[MemoryManager] = None
preference_engine: Optional[PreferenceEngine] = None

# Rate limiter state: key -> deque of timestamps (thread-safe)
_rate_limit_lock = threading.Lock()
_rate_limit_records: dict[str, collections.deque] = collections.defaultdict(collections.deque)


def reset_rate_limits() -> None:
    """Resets rate limit state for tests."""
    with _rate_limit_lock:
        _rate_limit_records.clear()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure strictly loopback host configuration
    host = os.getenv("HOST", "127.0.0.1").strip()
    if host in ("0.0.0.0", "", "::", "0:0:0:0:0:0:0:0"):
        logger.warning("Rejecting public host binding %s; enforcing loopback 127.0.0.1", host)
        os.environ["HOST"] = "127.0.0.1"
    logger.info("Context Passport API initialized on loopback %s", os.getenv("HOST", "127.0.0.1"))
    yield
    # Shutdown: clean up singleton connections and reset in-memory state
    global memory_manager, preference_engine
    if preference_engine is not None and hasattr(preference_engine, "client") and preference_engine.client:
        try:
            preference_engine.client.close()
        except Exception:
            pass
    if memory_manager is not None and hasattr(memory_manager, "collection") and memory_manager.collection is not None:
        try:
            memory_manager.collection.database.client.close()
        except Exception:
            pass
    memory_manager = None
    preference_engine = None
    reset_rate_limits()
    logger.info("Context Passport API shutdown complete")


app = FastAPI(
    title="Context Passport API",
    description="Privacy-first browser memory backend and self-improvement engine.",
    version="3.0.0",
    lifespan=lifespan,
)

# Explicit CORS configuration for the 3 target AI sites, extension, and localhost
ALLOWED_ORIGINS = [
    "https://chatgpt.com",
    "https://claude.ai",
    "https://gemini.google.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost",
    "http://127.0.0.1",
]
ALLOWED_ORIGIN_REGEX = (
    r"^(chrome-extension://[a-z0-9]+"
    r"|https://([a-zA-Z0-9-]+\.)*(chatgpt\.com|claude\.ai|gemini\.google\.com)"
    r"|http://(localhost|127\.0\.0\.1)(:\d+)?)$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# Exception Handlers for clean provider error messages without leaking secrets
@app.exception_handler(ProviderTimeoutError)
async def provider_timeout_handler(request: Request, exc: ProviderTimeoutError):
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={"detail": "Model provider request timed out. Please retry."},
    )


@app.exception_handler(ProviderRateLimitError)
async def provider_rate_limit_handler(request: Request, exc: ProviderRateLimitError):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Model provider rate limit exceeded. Please try again shortly."},
        headers={"Retry-After": "30"},
    )


@app.exception_handler(SpendingCapExceededError)
async def spending_cap_handler(request: Request, exc: SpendingCapExceededError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )


@app.exception_handler(ProviderOutageError)
async def provider_outage_handler(request: Request, exc: ProviderOutageError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Model provider service unavailable. Please retry later."},
    )


@app.exception_handler(InvalidProviderResponseError)
async def provider_invalid_resp_handler(request: Request, exc: InvalidProviderResponseError):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Model provider returned an invalid response."},
    )


@app.exception_handler(MissingConfigurationError)
async def provider_missing_config_handler(request: Request, exc: MissingConfigurationError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )


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
        client_ip = request.client.host if request.client else "127.0.0.1"
        auth_header = request.headers.get("authorization", "")
        bucket_key = auth_header[-16:] if len(auth_header) > 16 else client_ip

        now = time.time()
        max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", str(RATE_LIMIT_MAX_REQUESTS)))
        window = float(os.getenv("RATE_LIMIT_WINDOW", str(RATE_LIMIT_WINDOW)))

        with _rate_limit_lock:
            timestamps = _rate_limit_records[bucket_key]
            while timestamps and now - timestamps[0] > window:
                timestamps.popleft()

            if len(timestamps) >= max_requests:
                retry_after = max(1, int(window - (now - timestamps[0])))
                logger.warning("Rate limit exceeded for %s", bucket_key)
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": f"Rate limit exceeded. Maximum {max_requests} requests per minute."},
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(max_requests),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(timestamps[0] + window)),
                    },
                )
            timestamps.append(now)
            remaining = max(0, max_requests - len(timestamps))

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_MAX_REQUESTS)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

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
async def ready(request: Request) -> JSONResponse:
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

    if missing:
        return JSONResponse(
            {"status": "configuration_required", "missing": missing},
            status_code=503,
        )

    # In test environment, skip live network checks unless explicitly requested via query param
    check_connectivity = (
        request.query_params.get("check_connectivity", "").lower() == "true"
        or os.getenv("APP_ENV", "production").lower() != "test"
    )
    if not check_connectivity:
        return JSONResponse(
            {"status": "ready", "missing": []},
            status_code=200,
        )

    # External dependency connectivity checks
    disconnected: list[str] = []

    # 1. MongoDB Atlas connectivity check
    try:
        engine = get_pref_engine()
        if hasattr(engine, "client") and engine.client is not None:
            engine.client.admin.command("ping")
        else:
            from pymongo import MongoClient
            c = MongoClient(os.getenv("MONGODB_URI"), serverSelectionTimeoutMS=2000)
            c.admin.command("ping")
            c.close()
    except Exception as exc:
        logger.warning("MongoDB Atlas ping failed in /ready: %s", exc)
        disconnected.append("mongodb")

    # 2. Supabase Auth connectivity check
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if supabase_url and anon_key:
        try:
            s_resp = httpx.get(
                f"{supabase_url}/auth/v1/settings",
                headers={"apikey": anon_key},
                timeout=2.0,
            )
            if not (s_resp.is_success or s_resp.status_code in (200, 401, 403)):
                disconnected.append("supabase")
        except Exception as exc:
            logger.warning("Supabase ping failed in /ready: %s", exc)
            disconnected.append("supabase")

    if disconnected:
        return JSONResponse(
            {
                "status": "service_unavailable",
                "disconnected": disconnected,
                "detail": f"Service(s) unreachable: {', '.join(disconnected)}",
            },
            status_code=503,
        )

    return JSONResponse(
        {
            "status": "ready",
            "mongodb": "connected",
            "supabase": "connected",
            "provider": provider,
        },
        status_code=200,
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
        except ProviderTimeoutError as exc:
            logger.error("Provider timeout during memory save for user %s", user.user_id)
            engine.mark_event_failed(user.user_id, message_hash, "ProviderTimeoutError", event_id=req.event_id)
            raise HTTPException(status_code=504, detail="Model provider request timed out. Please retry.") from exc
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

    host = os.getenv("HOST", "127.0.0.1").strip()
    if host in ("0.0.0.0", "", "::", "0:0:0:0:0:0:0:0"):
        logger.warning("Public interface binding rejected for security; binding to 127.0.0.1")
        host = "127.0.0.1"
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting Context Passport API on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
