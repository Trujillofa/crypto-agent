from __future__ import annotations

import asyncio
from collections.abc import Sequence

import openai
from openai import AsyncOpenAI

from src.utils.logger import get_logger

_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)
_MAX_ATTEMPTS = 3


class XAIClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            timeout=timeout_seconds,
            max_retries=0,  # we manage retries ourselves
        )
        self._model = model
        self._logger = get_logger(self.__class__.__name__)

    async def chat(self, messages: Sequence[dict[str, str]]) -> str:
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                completion = await self._client.chat.completions.create(
                    model=self._model,
                    messages=list(messages),
                    temperature=0.2,
                )
                choices = getattr(completion, "choices", [])
                if choices:
                    message = getattr(choices[0], "message", None)
                    content = getattr(message, "content", None)
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                return "No response content returned by xAI API."
            except _RETRYABLE_ERRORS as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    delay = 2**attempt  # 1s, 2s
                    self._logger.warning(
                        "xAI request failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        _MAX_ATTEMPTS,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]
