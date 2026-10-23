from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import openai
from openai import AsyncOpenAI

from src.utils.logger import get_logger

_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)
_AUTH_ERRORS = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
)
_MAX_ATTEMPTS = 3


def _is_permanent_provider_error(exc: Exception) -> bool:
    """Detect auth/billing/plan exhaustion so poll loops do not hammer a dead key."""
    if isinstance(exc, _AUTH_ERRORS) or getattr(exc, "status_code", None) in {402, 403}:
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "insufficient balance",
            "no resource package",
            "credits exhausted",
            "spending limit",
            "billing limit",
            "quota exceeded",
            "usage limit reached",
        )
    )


@dataclass(frozen=True)
class ChatResult:
    """LLM reply plus which provider and model actually answered."""

    content: str
    provider: str  # "xai" | "deepseek" | "zai" | "opencode-go" | "ollama" | "nvidia"
    model: str


class XAIClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        fallback_api_key: str = "",
        fallback_model: str = "deepseek-chat",
        fallback_base_url: str = "https://api.deepseek.com/v1",
        base_url: str = "https://api.x.ai/v1",
        provider: str = "xai",
        opencode_go_api_key: str = "",
        opencode_go_base_url: str = "https://opencode.ai/zen/go/v1",
        xai_fallback_api_key: str = "",
        xai_fallback_model: str = "grok-4-1-fast-reasoning",
        ollama_api_key: str = "",
        ollama_model: str = "gemma4:31b",
        ollama_base_url: str = "https://ollama.com/v1",
        nvidia_api_key: str = "",
        nvidia_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b",
        nvidia_base_url: str = "https://integrate.api.nvidia.com/v1",
    ) -> None:
        self._provider = provider
        self._primary_name = {"deepseek": "DeepSeek", "zai": "Z.AI"}.get(provider, "xAI")
        self._skip_primary = False
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,  # we manage retries ourselves
        )
        self._model = model
        self._fallback_client = None
        self._fallback_model = fallback_model
        if fallback_api_key:
            self._fallback_client = AsyncOpenAI(
                api_key=fallback_api_key,
                base_url=fallback_base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
        self._opencode_client = None
        if opencode_go_api_key:
            self._opencode_client = AsyncOpenAI(
                api_key=opencode_go_api_key,
                base_url=opencode_go_base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
        self._opencode_model_date: date | None = None
        self._opencode_model: str | None = None
        self._opencode_model_lock = asyncio.Lock()
        self._opencode_disabled_date: date | None = None
        self._skip_fallback = False
        self._skip_xai_fallback = False
        self._xai_fallback_client = None
        self._xai_fallback_model = xai_fallback_model
        if xai_fallback_api_key and provider != "xai":
            self._xai_fallback_client = AsyncOpenAI(
                api_key=xai_fallback_api_key,
                base_url="https://api.x.ai/v1",
                timeout=timeout_seconds,
                max_retries=0,
            )
        self._ollama_client = (
            AsyncOpenAI(
                api_key=ollama_api_key,
                base_url=ollama_base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
            if ollama_api_key
            else None
        )
        self._ollama_model = ollama_model
        self._skip_ollama = False
        self._nvidia_client = (
            AsyncOpenAI(
                api_key=nvidia_api_key,
                base_url=nvidia_base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
            if nvidia_api_key
            else None
        )
        self._nvidia_model = nvidia_model
        self._skip_nvidia = False
        self._logger = get_logger(self.__class__.__name__)

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    async def chat(self, messages: Sequence[dict[str, str]]) -> ChatResult:
        primary_error: Exception | None = None
        if not self._skip_primary:
            try:
                content = await self._chat_with_client(
                    self._client,
                    self._model,
                    messages,
                    provider_name=self._primary_name,
                )
                return ChatResult(content=content, provider=self._provider, model=self._model)
            except _AUTH_ERRORS as exc:
                self._skip_primary = True
                primary_error = exc
                self._logger.warning(
                    "%s auth/permission denied (%s); skipping primary on later calls",
                    self._primary_name,
                    exc,
                )
            except Exception as exc:
                primary_error = exc
                self._skip_primary = _is_permanent_provider_error(exc)
                self._logger.warning(
                    "%s unavailable after retries; trying configured fallbacks",
                    self._primary_name,
                )

        today = datetime.now(UTC).date()
        opencode_error: Exception | None = None
        if self._opencode_client is not None and self._opencode_disabled_date != today:
            try:
                model = await self._resolve_opencode_free_model()
                content = await self._chat_with_client(
                    self._opencode_client,
                    model,
                    messages,
                    provider_name="OpenCode Go free model",
                )
                return ChatResult(content=content, provider="opencode-go", model=model)
            except Exception as exc:
                opencode_error = exc
                if _is_permanent_provider_error(exc):
                    self._opencode_disabled_date = today
                self._logger.warning("OpenCode Go free-model fallback failed: %s", exc)

        ollama_error: Exception | None = None
        if self._ollama_client is not None and not self._skip_ollama:
            try:
                content = await self._chat_with_client(
                    self._ollama_client,
                    self._ollama_model,
                    messages,
                    provider_name="Ollama Cloud",
                )
                return ChatResult(content=content, provider="ollama", model=self._ollama_model)
            except Exception as exc:
                ollama_error = exc
                self._skip_ollama = _is_permanent_provider_error(exc)
                self._logger.warning("Ollama fallback failed: %s", exc)

        nvidia_error: Exception | None = None
        if self._nvidia_client is not None and not self._skip_nvidia:
            try:
                content = await self._chat_with_client(
                    self._nvidia_client,
                    self._nvidia_model,
                    messages,
                    provider_name="NVIDIA NIM",
                )
                return ChatResult(content=content, provider="nvidia", model=self._nvidia_model)
            except Exception as exc:
                nvidia_error = exc
                self._skip_nvidia = _is_permanent_provider_error(exc)
                self._logger.warning("NVIDIA NIM fallback failed: %s", exc)

        xai_error: Exception | None = None
        if self._xai_fallback_client is not None and not self._skip_xai_fallback:
            try:
                content = await self._chat_with_client(
                    self._xai_fallback_client,
                    self._xai_fallback_model,
                    messages,
                    provider_name="xAI fallback",
                )
                return ChatResult(content=content, provider="xai", model=self._xai_fallback_model)
            except Exception as exc:
                xai_error = exc
                self._skip_xai_fallback = _is_permanent_provider_error(exc)
                self._logger.warning("xAI fallback failed: %s", exc)

        deepseek_error: Exception | None = None
        if self._fallback_client is not None and not self._skip_fallback:
            try:
                content = await self._chat_with_client(
                    self._fallback_client,
                    self._fallback_model,
                    messages,
                    provider_name="DeepSeek",
                )
                return ChatResult(content=content, provider="deepseek", model=self._fallback_model)
            except Exception as exc:
                deepseek_error = exc
                self._skip_fallback = _is_permanent_provider_error(exc)
                self._logger.warning("DeepSeek fallback failed: %s", exc)

        if deepseek_error is not None:
            raise deepseek_error
        if xai_error is not None:
            raise xai_error
        if nvidia_error is not None:
            raise nvidia_error
        if ollama_error is not None:
            raise ollama_error
        if opencode_error is not None:
            raise opencode_error
        if primary_error is not None:
            raise primary_error
        raise RuntimeError(
            f"{self._primary_name} is disabled and no fallback provider is configured"
        )

    async def _resolve_opencode_free_model(self) -> str:
        """Refresh the provider catalog once per UTC day and select only free IDs.

        Fail closed when the provider no longer advertises a free model; never
        silently fall back to a paid model from the same catalog.
        """
        if self._opencode_client is None:
            raise RuntimeError("OpenCode Go is not configured")
        today = datetime.now(UTC).date()
        if self._opencode_model_date == today:
            if self._opencode_model:
                return self._opencode_model
            raise RuntimeError("OpenCode Go catalog has no advertised free models")
        async with self._opencode_model_lock:
            if self._opencode_model_date == today:
                if self._opencode_model:
                    return self._opencode_model
                raise RuntimeError("OpenCode Go catalog has no advertised free models")
            response = await self._opencode_client.models.list()
            model_ids = sorted(
                model.id
                for model in response.data
                if re.search(r"(?:^|[-_.])free(?:$|[-_.])", model.id, re.IGNORECASE)
            )
            if not model_ids:
                self._opencode_model = None
                self._opencode_model_date = today
                raise RuntimeError("OpenCode Go catalog has no advertised free models")
            # Deterministic daily choice rotates across whatever the live catalog
            # currently advertises, without pinning a model that may disappear.
            selected = model_ids[today.toordinal() % len(model_ids)]
            self._opencode_model = selected
            self._opencode_model_date = today
            self._logger.info(
                "Selected OpenCode Go free model %s from %d daily catalog candidates",
                selected,
                len(model_ids),
            )
            return selected

    async def _chat_with_client(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        provider_name: str,
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    temperature=0.2,
                )
                choices = getattr(completion, "choices", [])
                if choices:
                    message = getattr(choices[0], "message", None)
                    content = getattr(message, "content", None)
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                return f"No response content returned by {provider_name} API."
            except _RETRYABLE_ERRORS as exc:
                if _is_permanent_provider_error(exc):
                    raise
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    delay = 2**attempt  # 1s, 2s
                    self._logger.warning(
                        "%s request failed (attempt %d/%d), retrying in %ds: %s",
                        provider_name,
                        attempt + 1,
                        _MAX_ATTEMPTS,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]
