from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

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


@dataclass(frozen=True)
class ChatResult:
    """LLM reply plus which provider and model actually answered."""

    content: str
    provider: str  # "xai" | "deepseek"
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
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
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
        self._logger = get_logger(self.__class__.__name__)

    async def chat(self, messages: Sequence[dict[str, str]]) -> ChatResult:
        try:
            content = await self._chat_with_client(
                self._client,
                self._model,
                messages,
                provider_name="xAI",
            )
            return ChatResult(content=content, provider="xai", model=self._model)
        except _AUTH_ERRORS as exc:
            if self._fallback_client is None:
                raise
            self._logger.warning(
                "xAI auth/permission denied (%s); trying DeepSeek fallback",
                exc,
            )
        except Exception:
            if self._fallback_client is None:
                raise
            self._logger.warning("xAI unavailable after retries; trying DeepSeek fallback")

        content = await self._chat_with_client(
            self._fallback_client,
            self._fallback_model,
            messages,
            provider_name="DeepSeek",
        )
        return ChatResult(content=content, provider="deepseek", model=self._fallback_model)

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
