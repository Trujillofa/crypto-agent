"""Selection-window ranking that must not look at holdout / test metrics.

Search and sweep printers call this helper so a better holdout number cannot
change candidate order. Holdout scores are stored for reporting only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RankedCandidate:
    """One candidate with a selection score and an unused holdout score."""

    name: str
    selection_score: float
    holdout_score: float = 0.0


def rank_by_selection_score(candidates: Sequence[RankedCandidate]) -> list[RankedCandidate]:
    """Return candidates ordered by selection score only.

    Ties break on name so the order is deterministic. ``holdout_score`` is
    ignored: swapping holdout numbers must not change this ranking.
    """
    return sorted(candidates, key=lambda item: (-item.selection_score, item.name))
