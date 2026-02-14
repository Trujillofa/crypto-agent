from __future__ import annotations

from collections.abc import Sequence

from openai import AsyncOpenAI


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
        )
        self._model = model

    async def chat(self, messages: Sequence[dict[str, str]]) -> str:
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
