"""Typed value objects and enums for A1 Phase-0 incentive-ops tooling.

All cross-module data uses these (no loose dicts). See handoff spec.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from eth_hash.auto import keccak


# Errors (fail closed)
class SuspectedSecretError(ValueError):
    """Raised when input looks like a private key or seed phrase. Never log the value."""

    pass


class EndpointNotAllowed(ValueError):
    """URL host+path not in allowlist."""

    pass


class NotSupportedReadOnly(RuntimeError):
    """Attempted operation outside read-only scope."""

    pass


class CapsExceeded(ValueError):
    """Ledger + candidate would breach PilotCaps."""

    pass


class ValidationError(ValueError):
    """Schema or input validation failure."""

    pass


class CaptureError(RuntimeError):
    """Capture or snapshot failure."""

    pass


# Enums (string values for YAML roundtrip and CLI)
class Criterion(StrEnum):
    TRUE = "true"
    FALSE = "false"
    MAYBE = "maybe"
    NA = "na"


class Classification(StrEnum):
    IN_CORE = "IN_CORE"
    IN_CONDITIONAL = "IN_CONDITIONAL"
    YIELD_ONLY = "YIELD_ONLY"
    DEFER = "DEFER"
    REJECT = "REJECT"


class Mechanism(StrEnum):
    FIXED_PER_IDENTITY_CAP = "fixed_per_identity_cap"
    LABOR_TASK = "labor_task"
    PRO_RATA_CAPITAL = "pro_rata_capital"
    PROPORTIONAL_POINTS = "proportional_points"
    LP_MARKET_MAKING = "lp_market_making"
    VOTE_INCENTIVE = "vote_incentive"
    CAPPED_CASHBACK = "capped_cashback"


class RewardType(StrEnum):
    ANNOUNCED_FIXED_TOKEN = "announced_fixed_token"
    ANNOUNCED_TOKEN_PRORATA = "announced_token_prorata"
    SPECULATIVE_POINTS = "speculative_points"
    CASHBACK = "cashback"
    NFT_OR_XP = "nft_or_xp"


class Actionability(StrEnum):
    ACTIONABLE = "ACTIONABLE"
    BLOCKED_NEEDS_CAPTURE = "BLOCKED_NEEDS_CAPTURE"
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"
    BLOCKED_TIER = "BLOCKED_TIER"
    BLOCKED_PROMOTION = "BLOCKED_PROMOTION"
    BLOCKED_CAPS = "BLOCKED_CAPS"


class ReviewerDecision(StrEnum):
    """Human reviewer gate for promotion. PENDING keeps every record non-ACTIONABLE."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_MORE_INFO = "NEEDS_MORE_INFO"


class EVReadiness(StrEnum):
    """Readiness of EV inputs for promotion decisions. READY requires full acceptable provenance."""

    UNREADY = "UNREADY"
    READY = "READY"


class JurisdictionStatus(StrEnum):
    """Jurisdiction eligibility status for verification audit. Only ELIGIBLE allows promotion."""

    ELIGIBLE = "ELIGIBLE"
    UNKNOWN = "UNKNOWN"
    INELIGIBLE = "INELIGIBLE"


@dataclass(frozen=True)
class SelectionCriteria:
    c1_fixed_or_capped: Criterion
    c2_terms_documented: Criterion
    c3_eligibility_public: Criterion
    c4_capital_bounded: Criterion
    c5_tail_named: Criterion
    c6_reward_rationale: Criterion

    def as_dict(self) -> dict[str, str]:
        return {
            "c1_fixed_or_capped": str(self.c1_fixed_or_capped),
            "c2_terms_documented": str(self.c2_terms_documented),
            "c3_eligibility_public": str(self.c3_eligibility_public),
            "c4_capital_bounded": str(self.c4_capital_bounded),
            "c5_tail_named": str(self.c5_tail_named),
            "c6_reward_rationale": str(self.c6_reward_rationale),
        }


@dataclass(frozen=True)
class ProgramRecord:
    """Frozen record loaded from registry YAML (validated)."""

    id: str
    name: str
    official_source_url: str
    secondary_url: str | None
    observed_at: date
    snapshot_sha256: str
    distribution_mechanism: Mechanism
    reward_type: RewardType
    capital_required: str
    lockup_vesting: str
    eligibility_window: str
    kyc_required: bool | str  # may be "maybe"
    jurisdiction_restrictions: bool | str
    sybil_policy: str
    chains_contracts: str
    exit_liquidity: str
    tail_risks: list[str]
    classification: Classification
    classification_reason: str
    selection_criteria: SelectionCriteria
    verification_status: str
    live_round_status: str
    review_expiry: date
    notes: str | None = None

    @property
    def is_captured(self) -> bool:
        return self.snapshot_sha256 != "PENDING_TOOL_CAPTURE" and bool(self.snapshot_sha256)


@dataclass(frozen=True)
class CaptureRecord:
    """Sidecar produced by capture.py (not mutating starter fixture)."""

    id: str
    snapshot_sha256: str
    captured_at: datetime
    raw_path: str  # relative path under research/a1-incentive-farming/snapshots/<id>/
    source_url: str


@dataclass(frozen=True)
class EVScenarioInputs:
    """Typed inputs for EV calculator. Validated at construction."""

    p_eligibility: float
    p_distribution: float
    reward_qty: float
    realizable_price: float
    liquidity_vesting_haircut: float
    base_yield: float
    gas_bridge_fees: float
    capital: float
    days: float
    benchmark_apy: float
    expected_loss_reserve: float
    manual_hours: float
    hourly_rate: float
    reward_announced: bool

    def __post_init__(self) -> None:
        # Range + non-neg + finite validation (fail closed, blocker #1 NaN/Inf)
        for name, val in [
            ("p_eligibility", self.p_eligibility),
            ("p_distribution", self.p_distribution),
        ]:
            if not (0.0 <= val <= 1.0):
                raise ValidationError(f"{name} must be in [0,1], got {val}")
            if not math.isfinite(val):
                raise ValidationError(f"{name} must be finite, got {val}")
        for name, val in [
            ("reward_qty", self.reward_qty),
            ("realizable_price", self.realizable_price),
            ("liquidity_vesting_haircut", self.liquidity_vesting_haircut),
            ("base_yield", self.base_yield),
            ("gas_bridge_fees", self.gas_bridge_fees),
            ("capital", self.capital),
            ("days", self.days),
            ("benchmark_apy", self.benchmark_apy),
            ("expected_loss_reserve", self.expected_loss_reserve),
            ("manual_hours", self.manual_hours),
            ("hourly_rate", self.hourly_rate),
        ]:
            if val < 0.0:
                raise ValidationError(f"{name} must be >= 0, got {val}")
            if not math.isfinite(val):
                raise ValidationError(f"{name} must be finite, got {val}")


@dataclass(frozen=True)
class DeadlineInputs:
    """Typed dates + horizon for deadlines monitor (registry-driven, offline)."""

    eligibility_open: date | None = None
    eligibility_close: date | None = None
    claim_date: date | None = None
    vesting_end: date | None = None
    review_expiry: date | None = None
    within_days: int = 7


@dataclass(frozen=True)
class PilotCaps:
    """Runtime-enforced pilot limits. Blocks (not just warns)."""

    total_usd: float = 1000.0
    per_program_usd: float = 250.0
    max_concurrent: int = 3

    def __post_init__(self) -> None:
        for name, val in [("total_usd", self.total_usd), ("per_program_usd", self.per_program_usd)]:
            if not math.isfinite(val) or val < 0:
                raise ValidationError(f"{name} must be finite and >=0, got {val}")
        if type(self.max_concurrent) is not int or self.max_concurrent < 0:
            raise ValidationError(f"max_concurrent must be integer >=0, got {self.max_concurrent}")


@dataclass(frozen=True)
class CapCheck:
    """Result of validate_caps (used by actionability)."""

    ok: bool
    total_after: float
    per_program_after: float
    concurrent_after: int
    reason: str | None = None


@dataclass(frozen=True)
class EligibilitySnapshot:
    """Read-only snapshot returned by eligibility lookups."""

    program_id: str
    address: str
    eligible: bool | None
    points_or_allocation: float | str | None
    last_updated: str | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


class Address:
    """Validated address value object. Address-only. Rejects secrets. EVM + Solana."""

    _EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
    _PRIVKEY_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
    _MNEMONIC_WORD_RE = re.compile(r"^[a-z]+$")
    _SOLANA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
    _BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    _BASE58_INDEX = {c: i for i, c in enumerate(_BASE58_ALPHABET)}

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("Address must be str")
        v = value.strip()
        if not v:
            raise ValueError("Address cannot be empty")
        if self._looks_like_secret(v):
            # Never include the value in the exception message or attributes
            raise SuspectedSecretError("Input looks like a private key or mnemonic seed; rejected")
        fmt = self._detect_format(v)
        if fmt is None:
            raise ValueError(
                "Invalid address format (expected EVM 0x + 40 hex or Solana base58 pubkey)"
            )
        if fmt == "evm":
            self._validate_eip55(v)
        self._value = self._normalize(v)

    @staticmethod
    def _looks_like_secret(v: str) -> bool:
        if Address._PRIVKEY_RE.match(v):
            return True
        # BIP-39 style: 12/15/18/24 space-separated lowercase words
        words = v.split()
        if len(words) in (12, 15, 18, 24):
            if all(Address._MNEMONIC_WORD_RE.match(w) for w in words):
                return True
        return False

    @staticmethod
    def _detect_format(v: str) -> str | None:
        if Address._EVM_RE.match(v):
            return "evm"
        if Address._is_solana_pubkey(v):
            return "solana"
        return None

    @staticmethod
    def _b58decode(value: str) -> bytes:
        num = 0
        for char in value:
            num = num * 58 + Address._BASE58_INDEX[char]
        pad = 0
        for char in value:
            if char == "1":
                pad += 1
            else:
                break
        decoded = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
        return b"\x00" * pad + decoded

    @staticmethod
    def _is_solana_pubkey(v: str) -> bool:
        if not Address._SOLANA_RE.match(v):
            return False
        try:
            return len(Address._b58decode(v)) == 32
        except KeyError:
            return False

    @staticmethod
    def _eip55_checksum(hex_addr: str) -> str:
        """Return ``0x`` + EIP-55 checksummed address for a 40-char hex string (any case)."""
        lower = hex_addr.lower()
        digest = keccak(lower.encode("ascii")).hex()
        out = ["0x"]
        for i, ch in enumerate(lower):
            if ch in "0123456789":
                out.append(ch)
            elif int(digest[i], 16) >= 8:
                out.append(ch.upper())
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _validate_eip55(v: str) -> None:
        hex_part = v[2:]
        # All-lower or all-upper are accepted as non-checksummed (incl. all-digit addresses).
        if hex_part == hex_part.lower() or hex_part == hex_part.upper():
            return
        if v != Address._eip55_checksum(hex_part):
            raise ValueError("Invalid EIP-55 checksum")

    @staticmethod
    def _normalize(v: str) -> str:
        # Keep case as provided (EIP-55 often mixed); lower for comparisons if needed
        return v

    @property
    def value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Address({self._value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Address):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


# Convenience for actionability classify results
@dataclass(frozen=True)
class ClassificationResult:
    derived_label: Classification
    rule_fired: str
    matches_recorded: bool
    recorded_label: Classification | None = None
    diff: str | None = None


@dataclass(frozen=True)
class ActionabilityResult:
    status: Actionability
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationRecord:
    """Typed verification sidecar (research/.../verifications/<id>.yaml).

    Records human attestation that live terms match the retained raw snapshot bytes.
    reviewer_decision must be APPROVED (and other flags true) to pass verified gate.
    PENDING forces non-ACTIONABLE per Day-0 rules.
    raw_evidence_path ensures durable bytes (hash alone insufficient).

    Added fields (blocker #5): round-specific URLs, jurisdiction, key dates for auditing live_round_open.
    """

    id: str
    snapshot_sha256: str
    terms_match_snapshot: bool
    live_round_open: bool
    reviewer_decision: ReviewerDecision
    verified_at: datetime | None = None
    raw_evidence_path: str | None = None
    official_round_terms_url: str | None = None
    captured_source_url: str | None = None
    jurisdiction_status: JurisdictionStatus = JurisdictionStatus.UNKNOWN
    eligibility_open: date | None = None
    eligibility_close: date | None = None
    claim_date: date | None = None
    vesting_end: date | None = None
    notes: str | None = None


@dataclass(frozen=True)
class EVInputsRecord:
    """Typed EV inputs sidecar for base-case per program.

    readiness="UNREADY" + per-field provenance required for valid Day-0 evidence (not invented values).
    Only READY inputs may contribute to positive base_ev.
    """

    id: str
    inputs: EVScenarioInputs
    reward_type: RewardType
    readiness: EVReadiness = EVReadiness.UNREADY
    provenance: dict[str, str] = field(default_factory=dict)
    notes: str | None = None
