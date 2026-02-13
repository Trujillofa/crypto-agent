"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import pytest

# Configure pytest-asyncio mode
pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
