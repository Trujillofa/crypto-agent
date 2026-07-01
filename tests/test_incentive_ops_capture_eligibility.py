"""Capture and eligibility: allowlist, Address, read-only, mocks for net.

- capture refuses non-allow / UNVERIFIED
- sha over raw bytes
- sidecar written (tmp)
- eligibility requires typed Address; rejects secrets
- only allowlisted hosts
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.incentive_ops.allowlist import is_allowed_url
from tools.incentive_ops.capture import capture_program
from tools.incentive_ops.eligibility import fetch_eligibility_str
from tools.incentive_ops.registry import load_registry
from tools.incentive_ops.types import (
    CaptureError,
    SuspectedSecretError,
)


def test_allowlist_permits_fixture_hosts():
    assert is_allowed_url("https://coinlist.co/token-launches")
    assert is_allowed_url("https://layer3.xyz/")
    assert is_allowed_url("https://academy.binance.com/ur-PK/bitcoin")
    assert is_allowed_url("https://www.kaito.ai/mindshare-arena/voices")
    assert not is_allowed_url("https://www.binance.com/en/learn-and-earn")
    assert not is_allowed_url("https://evil.com/steal")


def test_capture_refuses_unverified():
    recs, _ = load_registry(warn=False)
    bad = next(r for r in recs if "UNVERIFIED" in r.official_source_url)
    with pytest.raises(CaptureError):
        capture_program(bad)


@patch("tools.incentive_ops.http.httpx.Client")
def test_capture_uses_allowlisted_and_writes_sidecar(mock_client, tmp_path, monkeypatch):
    # setup mock response
    mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
    mock_resp.status_code = 200
    mock_resp.content = b"<html>fake terms for test</html>"
    mock_resp.raise_for_status = lambda: None

    recs, _ = load_registry(warn=False)
    good = next(r for r in recs if r.id == "coinlist-token-sale")
    # force allowlist check pass (it does), patch roots
    from tools.incentive_ops import capture as capmod

    monkeypatch.setattr(capmod, "CAPTURES_ROOT", tmp_path / "captures")
    monkeypatch.setattr(capmod, "SNAPSHOTS_ROOT", tmp_path / "snapshots")
    (tmp_path / "captures").mkdir()
    (tmp_path / "snapshots").mkdir()

    cap = capture_program(good, force=True)
    assert cap.snapshot_sha256
    assert cap.raw_path  # path recorded
    side = tmp_path / "captures" / f"{good.id}.yaml"
    assert side.exists()


def test_eligibility_address_only_and_rejects_secrets():
    with pytest.raises(SuspectedSecretError):
        fetch_eligibility_str("layer3-quests", "0x" + "deadbeef" * 8)
    # valid format
    snap = fetch_eligibility_str("layer3-quests", "0x0000000000000000000000000000000000000000")
    assert snap.program_id == "layer3-quests"
    assert snap.address.startswith("0x")


def test_allowlisted_get_blocks_non_allowlisted_host():
    """The shared GET client rejects a non-allowlisted host before any network I/O."""
    from tools.incentive_ops.http import allowlisted_get
    from tools.incentive_ops.types import EndpointNotAllowed

    with pytest.raises(EndpointNotAllowed):
        allowlisted_get("https://evil.com/steal")


def test_adapter_cannot_bypass_allowlist_via_shared_client():
    """An eligibility adapter that omits its own assert_allowed still cannot reach a
    non-allowlisted host, because the only GET path (allowlisted_get) enforces it (#124)."""
    from tools.incentive_ops import eligibility as eligmod
    from tools.incentive_ops.eligibility import fetch_eligibility_str, register_adapter
    from tools.incentive_ops.http import allowlisted_get
    from tools.incentive_ops.types import Address, EligibilitySnapshot, EndpointNotAllowed

    def _rogue_adapter(program_id: str, addr: Address) -> EligibilitySnapshot:
        # Deliberately no assert_allowed here; the shared client must still block it.
        resp = allowlisted_get("https://evil.com/points")
        return EligibilitySnapshot(
            program_id=program_id,
            address=str(addr),
            eligible=None,
            points_or_allocation=str(resp.status_code),
            last_updated=None,
            source="rogue",
            raw={},
        )

    register_adapter("rogue-program", _rogue_adapter)
    try:
        with pytest.raises(EndpointNotAllowed):
            fetch_eligibility_str("rogue-program", "0x0000000000000000000000000000000000000000")
    finally:
        eligmod._ADAPTERS.pop("rogue-program", None)


@patch("tools.incentive_ops.http.httpx.Client")
def test_allowlisted_get_rejects_3xx(mock_client):
    """3xx responses must raise even though httpx.raise_for_status() accepts them."""
    import httpx

    from tools.incentive_ops.http import allowlisted_get

    mock_resp = mock_client.return_value.__enter__.return_value.get.return_value
    mock_resp.status_code = 304
    mock_resp.raise_for_status = lambda: None
    mock_resp.request = httpx.Request("GET", "https://layer3.xyz/")

    with pytest.raises(httpx.HTTPStatusError, match="non-2xx \\(304\\)"):
        allowlisted_get("https://layer3.xyz/")
