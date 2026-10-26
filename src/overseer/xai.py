from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

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
_TRANSIENT_PROVIDER_COOLDOWN = timedelta(seconds=120)
_SENTIMENT_REQUEST_TIMEOUT_SECONDS = 10.0
_NVIDIA_SENTIMENT_REQUEST_TIMEOUT_SECONDS = 30.0  # NVIDIA NIM can take >20s to answer
_SENTIMENT_CHAIN_TIMEOUT_SECONDS = 50.0  # leaves headroom in the 60s poll cycle


def _is_permanent_provider_error(exc: Exception) -> bool:
    """Detect errors unlikely to recover without configuration/account changes.

    Do not treat generic quota/rate-limit messages as permanent: many providers
    apply short rolling windows, and those providers must be retried later.
    """
    if isinstance(exc, _AUTH_ERRORS) or getattr(exc, "status_code", None) in {401, 402, 403}:
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
        )
    )


def _provider_error_summary(exc: Exception) -> str:
    """Compact error type/status label for provider-client log messages."""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return f"{type(exc).__name__} (HTTP {status_code})"
    return type(exc).__name__


@dataclass(frozen=True)
class ChatResult:
    """LLM reply plus which provider and model actually answered."""

    content: str
    provider: str  # xAI, xAI fallback, DeepSeek, Z.AI, OpenCode Go, Ollama, or NVIDIA
    model: str


class ProviderResponseValidationError(ValueError):
    """A provider answered, but failed the consumer's response contract."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        super().__init__(f"{provider} returned an invalid response")


def _annotate_provider_error(exc: Exception, provider: str, model: str) -> None:
    """Preserve the provider/model that produced a final failed response."""
    if not getattr(exc, "provider", None):
        exc.provider = provider  # type: ignore[attr-defined]
    if not getattr(exc, "model", None):
        exc.model = model  # type: ignore[attr-defined]


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
        nvidia_model: str = "minimaxai/minimax-m3.1-flash",
        nvidia_base_url: str = "https://integrate.api.nvidia.com/v1",
    ) -> None:
        self._provider = provider
        self._primary_name = {"deepseek": "DeepSeek", "zai": "Z.AI"}.get(provider, "xAI")
        self._provider_disabled_dates: dict[str, date] = {}
        self._provider_retry_after: dict[str, datetime] = {}
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
        self._logger = get_logger(self.__class__.__name__)

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    def _provider_available(self, provider: str, *, honor_transient_cooldown: bool = True) -> bool:
        now = datetime.now(UTC)
        disabled_date = self._provider_disabled_dates.get(provider)
        if disabled_date == now.date():
            return False
        if disabled_date is not None:
            self._provider_disabled_dates.pop(provider, None)

        retry_after = self._provider_retry_after.get(provider) if honor_transient_cooldown else None
        if retry_after is not None and retry_after > now:
            return False
        if retry_after is not None:
            self._provider_retry_after.pop(provider, None)
        return True

    def _record_provider_failure(
        self, provider: str, exc: Exception, *, apply_transient_cooldown: bool = True
    ) -> None:
        now = datetime.now(UTC)
        provider_label = self._primary_name if provider == "primary" else provider
        if _is_permanent_provider_error(exc):
            self._provider_disabled_dates[provider] = now.date()
            self._provider_retry_after.pop(provider, None)
            self._logger.warning(
                "%s disabled until UTC day rollover after %s",
                provider_label,
                _provider_error_summary(exc),
            )
        elif apply_transient_cooldown:
            # 120s spans two 60s sentiment polling cycles, limiting retries for
            # throttled providers without suppressing normal overseer chat.
            self._provider_retry_after[provider] = now + _TRANSIENT_PROVIDER_COOLDOWN
            self._logger.info(
                "%s cooling down for %ds after %s",
                provider_label,
                int(_TRANSIENT_PROVIDER_COOLDOWN.total_seconds()),
                _provider_error_summary(exc),
            )

    async def chat_with_validator(
        self,
        messages: Sequence[dict[str, str]],
        response_validator: Callable[[str], object],
    ) -> ChatResult:
        try:
            async with asyncio.timeout(_SENTIMENT_CHAIN_TIMEOUT_SECONDS):
                return await self.chat(
                    messages,
                    response_validator=response_validator,
                    max_attempts=1,
                    request_timeout=_SENTIMENT_REQUEST_TIMEOUT_SECONDS,
                )
        except TimeoutError as exc:
            error = TimeoutError(
                f"Sentiment provider chain exceeded the {_SENTIMENT_CHAIN_TIMEOUT_SECONDS:.0f}s budget"
            )
            _annotate_provider_error(error, "provider-chain", "chain")
            raise error from exc

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        response_validator: Callable[[str], object] | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
        request_timeout: float | None = None,
    ) -> ChatResult:
        use_transient_cooldown = response_validator is not None
        primary_error: Exception | None = None
        if self._provider_available("primary", honor_transient_cooldown=use_transient_cooldown):
            try:
                content = await self._chat_with_client(
                    self._client,
                    self._model,
                    messages,
                    provider_name=self._primary_name,
                    max_attempts=max_attempts,
                    provider_id=self._provider,
                    response_validator=response_validator,
                    request_timeout=request_timeout,
                )
                return ChatResult(content=content, provider=self._provider, model=self._model)
            except _AUTH_ERRORS as exc:
                self._record_provider_failure(
                    "primary", exc, apply_transient_cooldown=use_transient_cooldown
                )
                primary_error = exc
                self._logger.warning(
                    "%s auth/permission denied (%s); skipping primary on later calls",
                    self._primary_name,
                    _provider_error_summary(exc),
                )
            except Exception as exc:
                primary_error = exc
                self._record_provider_failure(
                    "primary", exc, apply_transient_cooldown=use_transient_cooldown
                )
                self._logger.warning(
                    "%s unavailable after retries (%s); trying configured fallbacks",
                    self._primary_name,
                    _provider_error_summary(exc),
                )

        opencode_error: Exception | None = None
        if self._opencode_client is not None and self._provider_available(
            "opencode-go", honor_transient_cooldown=use_transient_cooldown
        ):
            try:
                model = await self._resolve_opencode_free_model(request_timeout=request_timeout)
                content = await self._chat_with_client(
                    self._opencode_client,
                    model,
                    messages,
                    provider_name="OpenCode Go free model",
                    max_attempts=max_attempts,
                    provider_id="opencode-go",
                    response_validator=response_validator,
                    request_timeout=request_timeout,
                )
                return ChatResult(content=content, provider="opencode-go", model=model)
            except Exception as exc:
                opencode_error = exc
                _annotate_provider_error(exc, "opencode-go", self._opencode_model or "catalog")
                self._record_provider_failure(
                    "opencode-go", exc, apply_transient_cooldown=use_transient_cooldown
                )
                self._logger.warning(
                    "OpenCode Go free-model fallback failed (%s)", _provider_error_summary(exc)
                )

        ollama_error: Exception | None = None
        if self._ollama_client is not None and self._provider_available(
            "ollama", honor_transient_cooldown=use_transient_cooldown
        ):
            try:
                content = await self._chat_with_client(
                    self._ollama_client,
                    self._ollama_model,
                    messages,
                    provider_name="Ollama Cloud",
                    max_attempts=max_attempts,
                    provider_id="ollama",
                    response_validator=response_validator,
                    request_timeout=request_timeout,
                )
                return ChatResult(content=content, provider="ollama", model=self._ollama_model)
            except Exception as exc:
                ollama_error = exc
                self._record_provider_failure(
                    "ollama", exc, apply_transient_cooldown=use_transient_cooldown
                )
                self._logger.warning("Ollama fallback failed (%s)", _provider_error_summary(exc))

        nvidia_error: Exception | None = None
        if self._nvidia_client is not None and self._provider_available(
            "nvidia", honor_transient_cooldown=use_transient_cooldown
        ):
            try:
                content = await self._chat_with_client(
                    self._nvidia_client,
                    self._nvidia_model,
                    messages,
                    provider_name="NVIDIA NIM",
                    max_attempts=max_attempts,
                    provider_id="nvidia",
                    response_validator=response_validator,
                    request_timeout=(
                        max(request_timeout, _NVIDIA_SENTIMENT_REQUEST_TIMEOUT_SECONDS)
                        if request_timeout is not None
                        else None
                    ),
                )
                return ChatResult(content=content, provider="nvidia", model=self._nvidia_model)
            except Exception as exc:
                nvidia_error = exc
                self._record_provider_failure(
                    "nvidia", exc, apply_transient_cooldown=use_transient_cooldown
                )
                self._logger.warning(
                    "NVIDIA NIM fallback failed (%s)", _provider_error_summary(exc)
                )

        xai_error: Exception | None = None
        if self._xai_fallback_client is not None and self._provider_available(
            "xai", honor_transient_cooldown=use_transient_cooldown
        ):
            try:
                content = await self._chat_with_client(
                    self._xai_fallback_client,
                    self._xai_fallback_model,
                    messages,
                    provider_name="xAI fallback",
                    max_attempts=max_attempts,
                    provider_id="xai-fallback",
                    response_validator=response_validator,
                    request_timeout=request_timeout,
                )
                return ChatResult(
                    content=content, provider="xai-fallback", model=self._xai_fallback_model
                )
            except Exception as exc:
                xai_error = exc
                self._record_provider_failure(
                    "xai", exc, apply_transient_cooldown=use_transient_cooldown
                )
                self._logger.warning("xAI fallback failed (%s)", _provider_error_summary(exc))

        deepseek_error: Exception | None = None
        if self._fallback_client is not None and self._provider_available(
            "deepseek", honor_transient_cooldown=use_transient_cooldown
        ):
            try:
                content = await self._chat_with_client(
                    self._fallback_client,
                    self._fallback_model,
                    messages,
                    provider_name="DeepSeek",
                    max_attempts=max_attempts,
                    provider_id="deepseek",
                    response_validator=response_validator,
                    request_timeout=request_timeout,
                )
                return ChatResult(content=content, provider="deepseek", model=self._fallback_model)
            except Exception as exc:
                deepseek_error = exc
                self._record_provider_failure(
                    "deepseek", exc, apply_transient_cooldown=use_transient_cooldown
                )
                self._logger.warning("DeepSeek fallback failed (%s)", _provider_error_summary(exc))

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
        error = RuntimeError("No configured AI provider is currently available")
        _annotate_provider_error(error, "provider-chain", "none")
        raise error

    async def _resolve_opencode_free_model(self, *, request_timeout: float | None = None) -> str:
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
            if request_timeout is None:
                response = await self._opencode_client.models.list()
            else:
                response = await self._opencode_client.models.list(timeout=request_timeout)
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
        max_attempts: int = _MAX_ATTEMPTS,
        provider_id: str | None = None,
        response_validator: Callable[[str], object] | None = None,
        request_timeout: float | None = None,
    ) -> str:
        chat_messages = cast(list[ChatCompletionMessageParam], list(messages))
        last_exc: Exception | None = None
        json_mode = response_validator is not None and (provider_id or self._provider) in {
            "deepseek",
            "ollama",
        }
        for attempt in range(max_attempts):
            try:
                if request_timeout is None and json_mode:
                    completion = await client.chat.completions.create(
                        model=model,
                        messages=chat_messages,
                        temperature=0.2,
                        max_tokens=256,
                        response_format={"type": "json_object"},
                    )
                elif request_timeout is None:
                    completion = await client.chat.completions.create(
                        model=model,
                        messages=chat_messages,
                        temperature=0.2,
                    )
                elif json_mode:
                    completion = await client.chat.completions.create(
                        model=model,
                        messages=chat_messages,
                        temperature=0.2,
                        timeout=request_timeout,
                        max_tokens=256,
                        response_format={"type": "json_object"},
                    )
                else:
                    completion = await client.chat.completions.create(
                        model=model,
                        messages=chat_messages,
                        temperature=0.2,
                        timeout=request_timeout,
                    )
                choices = getattr(completion, "choices", [])
                if not choices:
                    raise RuntimeError(f"No response content returned by {provider_name} API.")
                message = getattr(choices[0], "message", None)
                content = getattr(message, "content", None)
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError(f"No response content returned by {provider_name} API.")
                content = content.strip()
                if response_validator is not None:
                    try:
                        response_validator(content)
                    except Exception as exc:
                        raise ProviderResponseValidationError(
                            provider_id or provider_name, model
                        ) from exc
                return content
            except _RETRYABLE_ERRORS as exc:
                if _is_permanent_provider_error(exc):
                    _annotate_provider_error(exc, provider_id or provider_name, model)
                    raise
                last_exc = exc
                if attempt < max_attempts - 1:
                    delay = 2**attempt  # 1s, 2s
                    self._logger.warning(
                        "%s request failed (attempt %d/%d), retrying in %ds (%s)",
                        provider_name,
                        attempt + 1,
                        max_attempts,
                        delay,
                        _provider_error_summary(exc),
                    )
                    await asyncio.sleep(delay)
            except Exception as exc:
                _annotate_provider_error(exc, provider_id or provider_name, model)
                raise
        if last_exc is None:
            raise RuntimeError(f"{provider_name} call failed without an exception")
        _annotate_provider_error(last_exc, provider_id or provider_name, model)
        raise last_exc
