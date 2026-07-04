"""eligibility.py — read-only public eligibility/points lookups (address-only).

Address is the typed Address VO (rejects secrets). Every URL must pass allowlist.
GET only. Adapters per program (stub most in Phase 0).
"""

from __future__ import annotations

from collections.abc import Callable

from src.utils.logger import get_logger

from .http import allowlisted_get
from .types import Address, EligibilitySnapshot

logger = get_logger(__name__)

# Adapter: (program_id, address) -> EligibilitySnapshot
Adapter = Callable[[str, Address], EligibilitySnapshot]

_ADAPTERS: dict[str, Adapter] = {}


def register_adapter(program_id: str, adapter: Adapter) -> None:
    _ADAPTERS[program_id] = adapter


def _default_stub(program_id: str, addr: Address) -> EligibilitySnapshot:
    # Phase 0: most programs have no simple public unauth address lookup or require login.
    # Return neutral; human will use explorer manually or future adapter.
    return EligibilitySnapshot(
        program_id=program_id,
        address=str(addr),
        eligible=None,
        points_or_allocation=None,
        last_updated=None,
        source="stub",
        raw={"note": "no public adapter or requires auth; use explorer"},
    )


def _example_layer3_adapter(program_id: str, addr: Address) -> EligibilitySnapshot:
    # Example adapter using allowlisted host. In real would query specific quest API if public.
    # For demo we GET the program page (allowlisted) and report no structured points.
    # The GET goes through the shared allowlisted client, so the allowlist is
    # enforced even though this adapter no longer calls assert_allowed itself.
    url = "https://layer3.xyz/"
    try:
        r = allowlisted_get(url, timeout=15.0)
        return EligibilitySnapshot(
            program_id=program_id,
            address=str(addr),
            eligible=None,
            points_or_allocation="see site (XP not public unauth)",
            last_updated=None,
            source=url,
            raw={"status": r.status_code},
        )
    except Exception as e:
        logger.warning("layer3 adapter fetch fail: %s", e)
        return _default_stub(program_id, addr)


# Ship 1-2 realish examples
register_adapter("layer3-quests", _example_layer3_adapter)
register_adapter("galxe-quests-oat", _default_stub)
register_adapter("testnet-incentive-archetype", _default_stub)


def _get_adapter(program_id: str) -> Adapter:
    return _ADAPTERS.get(program_id, _default_stub)


def fetch_eligibility(program_id: str, address: Address) -> EligibilitySnapshot:
    """Main entry. address must be Address instance (validated upstream)."""
    if not isinstance(address, Address):
        # fail closed
        raise ValueError("fetch_eligibility requires typed Address, not str")
    adapter = _get_adapter(program_id)
    snap = adapter(program_id, address)
    logger.info(
        "eligibility lookup %s %s -> eligible=%s", program_id, str(address)[:10], snap.eligible
    )
    return snap


def fetch_eligibility_str(program_id: str, addr_str: str) -> EligibilitySnapshot:
    """Convenience that constructs+validates Address (rejects secrets)."""
    addr = Address(addr_str)  # may raise SuspectedSecretError
    return fetch_eligibility(program_id, addr)


# Example of per-program allowlisted URL builder (future adapters extend)
def build_public_url(program_id: str, address: Address) -> str | None:
    # Stub: return None for most; real would map e.g. to explorer.
    if program_id == "layer3-quests":
        return "https://layer3.xyz/"
    return None
