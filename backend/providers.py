"""Provider abstraction layer for fact extraction, embeddings, and validation.

Supports:
- OpenRouter extraction with primary (GLM 5.3 Flash) and fallback (Gemini 2.5 Flash Lite)
- OpenRouter embeddings (text-embedding-3-small, 1536 dims)
- Optional feature-flagged Jev (TypeSafe AI) validation pass
- Thread-safe usage tracking and spending caps
- Strict privacy: never log model keys, auth tokens, or message bodies
- Fail-closed error handling with safe, non-leaking exceptions
- Mock implementations for offline testing without real credentials
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from security import contains_secret

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safe Provider Exceptions (never leak API keys, tokens, or message text)
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """Base exception for all model provider failures."""
    pass


class ProviderTimeoutError(ProviderError):
    """Raised when a provider call exceeds its deadline."""
    pass


class ProviderRateLimitError(ProviderError):
    """Raised when a provider returns HTTP 429 Too Many Requests."""
    pass


class ProviderOutageError(ProviderError):
    """Raised when a provider returns HTTP 5xx or server failure."""
    pass


class InvalidProviderResponseError(ProviderError):
    """Raised when provider output is malformed, empty, or violates schema."""
    pass


class SpendingCapExceededError(ProviderError):
    """Raised when cumulative model usage exceeds the configured spending cap."""
    pass


class MissingConfigurationError(ProviderError):
    """Raised when an active provider is missing required credentials."""
    pass


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExtractionResult:
    facts: List[str]
    model_used: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    fallback_used: bool = False


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    classification: Optional[str] = None
    confidence: float = 1.0
    reason: str = ""


# ---------------------------------------------------------------------------
# Usage & Spending Cap Tracker (Thread-safe)
# ---------------------------------------------------------------------------
class UsageTracker:
    """Tracks token usage and estimated spend across model calls."""

    # Approximate rates per 1M tokens (USD)
    # GLM 5.3 Flash: ~$0.10 / 1M prompt, $0.10 / 1M completion
    # Gemini 2.5 Flash Lite: ~$0.075 / 1M prompt, $0.30 / 1M completion
    # text-embedding-3-small: ~$0.02 / 1M tokens
    DEFAULT_RATES: Dict[str, Tuple[float, float]] = {
        "z-ai/glm-5.3-flash": (0.10, 0.10),
        "openrouter/glm-5.3-flash": (0.10, 0.10),
        "google/gemini-2.5-flash-lite": (0.075, 0.30),
        "openai/text-embedding-3-small": (0.02, 0.0),
    }

    def __init__(self, spending_cap_usd: float = 4.50):
        self._lock = threading.Lock()
        self.spending_cap_usd = spending_cap_usd
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_embedding_tokens = 0
        self.total_estimated_cost_usd = 0.0
        self.total_calls = 0

    def check_cap(self) -> None:
        with self._lock:
            if self.total_estimated_cost_usd >= self.spending_cap_usd:
                raise SpendingCapExceededError(
                    f"Spending cap of ${self.spending_cap_usd:.2f} reached "
                    f"(current spend: ${self.total_estimated_cost_usd:.4f})."
                )

    def record_chat_usage(self, prompt_tokens: int, completion_tokens: int, model: str) -> float:
        rate_prompt, rate_completion = self.DEFAULT_RATES.get(model, (0.15, 0.30))
        cost = (prompt_tokens * rate_prompt + completion_tokens * rate_completion) / 1_000_000.0
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_estimated_cost_usd += cost
            self.total_calls += 1
        return cost

    def record_embedding_usage(self, tokens: int, model: str) -> float:
        rate, _ = self.DEFAULT_RATES.get(model, (0.02, 0.0))
        cost = (tokens * rate) / 1_000_000.0
        with self._lock:
            self.total_embedding_tokens += tokens
            self.total_estimated_cost_usd += cost
            self.total_calls += 1
        return cost

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_calls": self.total_calls,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_embedding_tokens": self.total_embedding_tokens,
                "total_estimated_cost_usd": round(self.total_estimated_cost_usd, 6),
                "spending_cap_usd": self.spending_cap_usd,
                "cap_exceeded": self.total_estimated_cost_usd >= self.spending_cap_usd,
            }

    def reset(self) -> None:
        with self._lock:
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0
            self.total_embedding_tokens = 0
            self.total_estimated_cost_usd = 0.0
            self.total_calls = 0


# Global singleton tracker
global_usage_tracker = UsageTracker()


# ---------------------------------------------------------------------------
# Output & Schema Validation Helpers
# ---------------------------------------------------------------------------
def parse_and_validate_facts_json(raw_output: str) -> List[str]:
    """
    Validates that raw_output contains a valid JSON array of non-empty strings.
    Fails closed if the output is malformed, markdown-wrapped, or wrong schema.
    """
    if not raw_output or not raw_output.strip():
        raise InvalidProviderResponseError("Provider returned empty response")

    text = raw_output.strip()
    # Strip markdown code blocks if model included ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidProviderResponseError(f"Provider output is not valid JSON: {exc.msg}") from exc

    if not isinstance(data, list):
        raise InvalidProviderResponseError(
            f"Provider output must be a JSON array, got {type(data).__name__}"
        )

    facts: List[str] = []
    for item in data:
        if not isinstance(item, str):
            raise InvalidProviderResponseError(
                f"Fact item must be a string, got {type(item).__name__}"
            )
        cleaned = item.strip()
        if cleaned:
            # Defense-in-depth: If extracted fact contains recognizable secret, skip it
            if contains_secret(cleaned):
                logger.info("Screened secret credential from extracted fact candidate")
                continue
            facts.append(cleaned)

    return facts


# ---------------------------------------------------------------------------
# Abstract Interfaces
# ---------------------------------------------------------------------------
class FactExtractor(ABC):
    @abstractmethod
    def extract_facts(self, text: str, user_id: str) -> ExtractionResult:
        """Extract durable facts from user message. Fails closed on error."""
        pass


class Embedder(ABC):
    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate unit-normalized embedding vector. Fails closed on error."""
        pass


class ValidatorPass(ABC):
    @abstractmethod
    def is_enabled(self) -> bool:
        """Whether this validation pass is active."""
        pass

    @abstractmethod
    def validate(self, text: str, context: Optional[Dict[str, Any]] = None) -> ValidationResult:
        """Validate classification or sensitivity."""
        pass


# ---------------------------------------------------------------------------
# OpenRouter Fact Extractor Implementation
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM_PROMPT = (
    "You are a personal fact extraction engine for a privacy-first memory assistant. "
    "Your job is to read user-authored messages and extract clear, durable, standalone facts "
    "about the user (e.g. preferences, constraints, roles, tools, frameworks, dietary rules). "
    "Output ONLY a JSON array of strings, for example: [\"User prefers dark mode\", \"User builds in TypeScript\"]. "
    "If the message contains no durable personal facts, output an empty JSON array: []. "
    "Never output preamble, markdown fences, or explanation."
)


class OpenRouterExtractor(FactExtractor):
    """
    OpenRouter fact extractor with primary model and bounded fallback.
    Never logs message bodies or API keys.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        primary_model: str = "z-ai/glm-5.3-flash",
        fallback_model: str = "google/gemini-2.5-flash-lite",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        usage_tracker: Optional[UsageTracker] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.usage_tracker = usage_tracker or global_usage_tracker
        self._custom_client = http_client

    def _call_model(self, model: str, text: str, client: httpx.Client) -> Tuple[str, int, int]:
        self.usage_tracker.check_cap()

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://contextpassport.local",
            "X-Title": "Context Passport",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
        }

        attempts = 0
        last_error: Optional[Exception] = None

        while attempts < self.max_retries:
            attempts += 1
            try:
                resp = client.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise InvalidProviderResponseError("No completion choices in response")
                    content = choices[0].get("message", {}).get("content", "")
                    usage = data.get("usage", {})
                    p_tokens = usage.get("prompt_tokens", 0)
                    c_tokens = usage.get("completion_tokens", 0)
                    self.usage_tracker.record_chat_usage(p_tokens, c_tokens, model)
                    return content, p_tokens, c_tokens
                elif resp.status_code == 429:
                    raise ProviderRateLimitError(f"OpenRouter rate limit on model {model}")
                elif resp.status_code >= 500:
                    raise ProviderOutageError(f"OpenRouter server error {resp.status_code} on model {model}")
                else:
                    raise ProviderError(f"OpenRouter returned status {resp.status_code} for model {model}")
            except httpx.TimeoutException as exc:
                last_error = ProviderTimeoutError(f"OpenRouter request timed out for model {model}")
            except (ProviderRateLimitError, ProviderOutageError, ProviderError) as exc:
                last_error = exc
                if isinstance(exc, ProviderRateLimitError):
                    break  # Don't spin immediately on 429; trigger fallback
            except Exception as exc:
                last_error = ProviderError(f"OpenRouter request failed: {type(exc).__name__}")

        if last_error:
            raise last_error
        raise ProviderError(f"OpenRouter failed for model {model}")

    def extract_facts(self, text: str, user_id: str) -> ExtractionResult:
        if not self.api_key:
            raise MissingConfigurationError("OPENROUTER_API_KEY is not configured")

        client = self._custom_client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._custom_client is None

        try:
            # 1. Attempt primary model
            try:
                raw_output, p_tokens, c_tokens = self._call_model(self.primary_model, text, client)
                facts = parse_and_validate_facts_json(raw_output)
                return ExtractionResult(
                    facts=facts,
                    model_used=self.primary_model,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    fallback_used=False,
                )
            except (ProviderTimeoutError, ProviderRateLimitError, ProviderOutageError, InvalidProviderResponseError) as primary_exc:
                logger.warning(
                    "Primary extraction model %s failed (%s); attempting fallback %s",
                    self.primary_model,
                    primary_exc.__class__.__name__,
                    self.fallback_model,
                )

            # 2. Attempt fallback model
            try:
                raw_output, p_tokens, c_tokens = self._call_model(self.fallback_model, text, client)
                facts = parse_and_validate_facts_json(raw_output)
                return ExtractionResult(
                    facts=facts,
                    model_used=self.fallback_model,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    fallback_used=True,
                )
            except Exception as fallback_exc:
                logger.error(
                    "Fallback extraction model %s also failed (%s)",
                    self.fallback_model,
                    fallback_exc.__class__.__name__,
                )
                raise ProviderError(
                    f"Fact extraction failed across primary ({self.primary_model}) "
                    f"and fallback ({self.fallback_model})"
                ) from fallback_exc

        finally:
            if close_client:
                client.close()


# ---------------------------------------------------------------------------
# OpenRouter Embedder Implementation
# ---------------------------------------------------------------------------
class OpenRouterEmbedder(Embedder):
    """
    OpenRouter embedder using openai/text-embedding-3-small (1536 dimensions).
    Validates output vector dimensions and values.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "openai/text-embedding-3-small",
        dimensions: int = 1536,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        usage_tracker: Optional[UsageTracker] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.usage_tracker = usage_tracker or global_usage_tracker
        self._custom_client = http_client

    def embed(self, text: str) -> List[float]:
        if not self.api_key:
            raise MissingConfigurationError("OPENROUTER_API_KEY is not configured for embeddings")

        self.usage_tracker.check_cap()

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://contextpassport.local",
            "X-Title": "Context Passport",
        }
        payload = {
            "model": self.model,
            "input": text,
            "dimensions": self.dimensions,
        }

        client = self._custom_client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._custom_client is None

        try:
            attempts = 0
            while attempts < self.max_retries:
                attempts += 1
                try:
                    resp = client.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
                    if resp.status_code == 200:
                        data = resp.json()
                        embed_data = data.get("data", [])
                        if not embed_data:
                            raise InvalidProviderResponseError("Embeddings response contained no vector data")
                        vector = embed_data[0].get("embedding", [])
                        if len(vector) != self.dimensions:
                            raise InvalidProviderResponseError(
                                f"Expected {self.dimensions} dimensions, got {len(vector)}"
                            )
                        tokens = data.get("usage", {}).get("total_tokens", len(text.split()))
                        self.usage_tracker.record_embedding_usage(tokens, self.model)
                        return vector
                    elif resp.status_code == 429:
                        raise ProviderRateLimitError("OpenRouter embedding rate limit reached")
                    elif resp.status_code >= 500:
                        raise ProviderOutageError(f"OpenRouter embedding server error {resp.status_code}")
                    else:
                        raise ProviderError(f"OpenRouter embedding status {resp.status_code}")
                except httpx.TimeoutException as exc:
                    if attempts >= self.max_retries:
                        raise ProviderTimeoutError("OpenRouter embedding request timed out") from exc
                except (ProviderRateLimitError, ProviderOutageError, ProviderError):
                    raise
                except Exception as exc:
                    if attempts >= self.max_retries:
                        raise ProviderError(f"OpenRouter embedding error: {type(exc).__name__}") from exc
            raise ProviderError("OpenRouter embedding failed")
        finally:
            if close_client:
                client.close()


# ---------------------------------------------------------------------------
# Jev (TypeSafe AI) Feature-Flagged Validator
# ---------------------------------------------------------------------------
class JevValidator(ValidatorPass):
    """
    TypeSafe AI Jev validation pass for confidence-gated sensitivity verification.
    Disabled by default; feature-flagged behind enable_jev_validation.
    """

    def __init__(
        self,
        enabled: bool = False,
        api_key: str = "",
        api_url: str = "https://api.typesafe.ai/v1/systemone",
        timeout_seconds: float = 15.0,
        http_client: Optional[httpx.Client] = None,
    ):
        self._enabled = enabled
        self.api_key = api_key
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self._custom_client = http_client

    def is_enabled(self) -> bool:
        return self._enabled and bool(self.api_key)

    def validate(self, text: str, context: Optional[Dict[str, Any]] = None) -> ValidationResult:
        if not self.is_enabled():
            return ValidationResult(is_valid=True, reason="Jev validation is disabled")

        client = self._custom_client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._custom_client is None

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "state": text,
                "question": "Does this text contain sensitive personal information (such as health, medical, allergies, credentials)?",
                "type": "noul",
            }
            resp = client.post(self.api_url, headers=headers, json=payload, timeout=self.timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                is_sensitive = data.get("decision", False)
                confidence = float(data.get("confidence", 1.0))
                classification = "sensitive" if is_sensitive else "general"
                return ValidationResult(
                    is_valid=True,
                    classification=classification,
                    confidence=confidence,
                    reason="Jev validation complete",
                )
            else:
                logger.warning("Jev validation returned status %d; failing open to standard classification", resp.status_code)
                return ValidationResult(is_valid=True, reason="Jev call failed; bypassed safely")
        except Exception as exc:
            logger.warning("Jev validation error (%s); bypassed safely", type(exc).__name__)
            return ValidationResult(is_valid=True, reason="Jev error; bypassed safely")
        finally:
            if close_client:
                client.close()


# ---------------------------------------------------------------------------
# Mock Implementations for Tests (Zero External Calls)
# ---------------------------------------------------------------------------
class MockExtractor(FactExtractor):
    """Configurable mock extractor for testing without paid API credentials."""

    def __init__(
        self,
        default_facts: Optional[List[str]] = None,
        fail_mode: Optional[str] = None,
        primary_model: str = "z-ai/glm-5.3-flash",
        fallback_model: str = "google/gemini-2.5-flash-lite",
    ):
        self.default_facts = default_facts if default_facts is not None else ["Mock extracted fact"]
        self.fail_mode = fail_mode  # None, 'invalid_json', 'empty', 'timeout', '429', 'outage', 'fallback_success'
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.call_count = 0
        self.calls: List[Dict[str, Any]] = []

    def extract_facts(self, text: str, user_id: str) -> ExtractionResult:
        self.call_count += 1
        self.calls.append({"text_length": len(text), "user_id": user_id, "mode": self.fail_mode})

        if self.fail_mode == "invalid_json":
            raise InvalidProviderResponseError("Provider output is not valid JSON: Expecting value")
        elif self.fail_mode == "empty":
            raise InvalidProviderResponseError("Provider returned empty response")
        elif self.fail_mode == "timeout":
            raise ProviderTimeoutError(f"OpenRouter request timed out for model {self.primary_model}")
        elif self.fail_mode == "429":
            raise ProviderRateLimitError(f"OpenRouter rate limit on model {self.primary_model}")
        elif self.fail_mode == "outage":
            raise ProviderOutageError(f"OpenRouter server error 503 on model {self.primary_model}")
        elif self.fail_mode == "fallback_success":
            return ExtractionResult(
                facts=["Fallback recovered fact"],
                model_used=self.fallback_model,
                prompt_tokens=15,
                completion_tokens=5,
                fallback_used=True,
            )

        return ExtractionResult(
            facts=list(self.default_facts),
            model_used=self.primary_model,
            prompt_tokens=20,
            completion_tokens=10,
            fallback_used=False,
        )


class MockEmbedder(Embedder):
    """Deterministic mock embedder returning unit-normalized 1536d vectors."""

    def __init__(self, dimensions: int = 1536):
        self.dimensions = dimensions
        self.call_count = 0

    def embed(self, text: str) -> List[float]:
        self.call_count += 1
        # Use existing deterministic embedding generator
        from memory_manager import generate_deterministic_embedding
        return generate_deterministic_embedding(text, self.dimensions)


class MockValidator(ValidatorPass):
    """Mock validator for testing Jev gating."""

    def __init__(self, enabled: bool = False, classification: str = "general"):
        self._enabled = enabled
        self.classification = classification
        self.call_count = 0

    def is_enabled(self) -> bool:
        return self._enabled

    def validate(self, text: str, context: Optional[Dict[str, Any]] = None) -> ValidationResult:
        self.call_count += 1
        if not self._enabled:
            return ValidationResult(is_valid=True, reason="Jev disabled")
        return ValidationResult(
            is_valid=True,
            classification=self.classification,
            confidence=0.98,
            reason="Mock Jev validation",
        )


# ---------------------------------------------------------------------------
# Provider Factories
# ---------------------------------------------------------------------------
def create_extractor(settings: Optional[Any] = None, *, fail_mode: Optional[str] = None) -> Optional[FactExtractor]:
    """Factory creating the configured FactExtractor, or None if unconfigured."""
    if fail_mode is not None:
        return MockExtractor(fail_mode=fail_mode)

    use_mock = os.getenv("USE_MOCK_EXTRACTOR", "false").lower() == "true"
    if use_mock:
        return MockExtractor()

    if settings is None:
        try:
            from settings import load_settings
            settings = load_settings(require_gemini=False)
        except Exception:
            settings = None

    if settings is None:
        return None

    provider = getattr(settings, "llm_provider", "openrouter")
    openrouter_api_key = getattr(settings, "openrouter_api_key", None)
    if provider == "openrouter" and openrouter_api_key:
        return OpenRouterExtractor(
            api_key=openrouter_api_key,
            base_url=getattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
            primary_model=getattr(settings, "extraction_primary_model", "z-ai/glm-5.3-flash"),
            fallback_model=getattr(settings, "extraction_fallback_model", "google/gemini-2.5-flash-lite"),
            timeout_seconds=getattr(settings, "provider_timeout_seconds", 30.0),
        )
    return None


def create_embedder(settings: Optional[Any] = None) -> Optional[Embedder]:
    """Factory creating the configured Embedder, or None if unconfigured."""
    use_mock = os.getenv("USE_MOCK_EMBEDDER", "false").lower() == "true"
    if use_mock:
        dims = int(os.getenv("EMBEDDING_DIMS", "1536"))
        return MockEmbedder(dimensions=dims)

    if settings is None:
        try:
            from settings import load_settings
            settings = load_settings(require_gemini=False)
        except Exception:
            settings = None

    if settings is None:
        return None

    provider = getattr(settings, "llm_provider", "openrouter")
    openrouter_api_key = getattr(settings, "openrouter_api_key", None)
    dims = getattr(settings, "embedding_dims", 1536)
    if provider == "openrouter" and openrouter_api_key:
        return OpenRouterEmbedder(
            api_key=openrouter_api_key,
            base_url=getattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1"),
            model=getattr(settings, "embedding_model", "openai/text-embedding-3-small"),
            dimensions=dims,
            timeout_seconds=getattr(settings, "provider_timeout_seconds", 30.0),
        )
    return None


def create_validator(settings: Optional[Any] = None) -> ValidatorPass:
    """Factory creating the configured ValidatorPass (Jev or Mock)."""
    if settings is None:
        try:
            from settings import load_settings
            settings = load_settings(require_gemini=False)
        except Exception:
            settings = None

    enabled = getattr(settings, "enable_jev_validation", False) if settings else False
    api_key = getattr(settings, "jev_api_key", "") if settings else ""
    return JevValidator(
        enabled=bool(enabled and api_key),
        api_key=api_key or "",
        api_url=getattr(settings, "jev_api_url", "https://api.typesafe.ai/v1/systemone") if settings else "https://api.typesafe.ai/v1/systemone",
        timeout_seconds=getattr(settings, "provider_timeout_seconds", 15.0) if settings else 15.0,
    )

