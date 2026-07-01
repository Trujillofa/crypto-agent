"""http.py — the single read-only, allowlisted GET path for incentive-ops.

Every outbound GET in this package MUST go through ``allowlisted_get`` so the
endpoint allowlist cannot be bypassed by a module — or a future eligibility
adapter — that forgets to call ``assert_allowed``. GET only, no redirects
(an allowlisted host must not be able to 30x us onto a non-allowlisted one),
no auth.
"""

from __future__ import annotations

import httpx

from src.utils.logger import get_logger

from .allowlist import assert_allowed

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0


def allowlisted_get(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> httpx.Response:
    """Perform a read-only GET, enforcing the endpoint allowlist first.

    The ONLY sanctioned way for incentive-ops modules to issue an HTTP GET.
    Raises ``EndpointNotAllowed`` (before any network I/O) if ``url`` is not
    allowlisted, never follows redirects, and raises ``httpx.HTTPError`` on a
    transport error or non-2xx status. Callers do their own body/status
    validation on the returned response.
    """
    assert_allowed(url)
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        resp = client.get(url)
    resp.raise_for_status()
    return resp
