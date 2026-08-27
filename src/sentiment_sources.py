"""Provider-neutral sentiment source taxonomy.

Stdlib only. Safe to import from the standalone sentiment report without
loading ``src.strategy``.
"""

from __future__ import annotations

SENTIMENT_ANSWERED_SOURCES = frozenset({"xai_live", "deepseek_fallback", "zai_live"})
SENTIMENT_ERROR_SOURCES = frozenset({"xai_error_fallback", "error_fallback"})
SENTIMENT_NO_ANSWER_SOURCES = SENTIMENT_ERROR_SOURCES | frozenset({"neutral_fallback"})


def is_answered_sentiment_source(source: str) -> bool:
    """True for a provider answer (xAI, Z.AI, or successful DeepSeek), not a fallback."""
    return source in SENTIMENT_ANSWERED_SOURCES
