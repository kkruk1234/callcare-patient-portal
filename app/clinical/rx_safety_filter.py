from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.clinical.med_exclusions_parser import MedExclusionFlags


@dataclass(frozen=True)
class MedCandidate:
    name: str                 # e.g. "azithromycin"
    class_name: str           # e.g. "macrolide"
    notes: str                # free text
    tags: List[str]           # e.g. ["qt_risk", "pregnancy_avoid"]
    evidence_keys: List[str]  # keys supporting this option


def filter_med_candidates(candidates: List[MedCandidate], flags: MedExclusionFlags) -> List[MedCandidate]:
    """
    Conservative deterministic filtering.
    Removes candidates that conflict with patient exclusions.
    """
    safe: List[MedCandidate] = []

    for c in candidates:
        n = (c.name or "").lower()
        cls = (c.class_name or "").lower()
        tags = set((t or "").lower() for t in (c.tags or []))

        # Allergy keyword block
        if any((kw in n) or (kw in cls) for kw in flags.allergy_keywords):
            continue

        # Pregnancy blocks (conservative)
        if flags.pregnancy_possible and ("pregnancy_avoid" in tags or "teratogen" in tags):
            continue

        # QT risk blocks (conservative)
        if flags.qt_risk and ("qt_risk" in tags or cls in {"macrolide", "fluoroquinolone"}):
            continue

        # Renal blocks
        if flags.renal and ("renal_avoid" in tags):
            continue

        # Liver blocks
        if flags.liver and ("liver_avoid" in tags):
            continue

        # Anticoag/GI bleed blocks (mainly NSAIDs)
        if (flags.anticoagulant or flags.gi_bleed_ulcer) and (cls == "nsaid" or "nsaid" in tags):
            continue

        safe.append(c)

    # Deduplicate by name
    seen = set()
    out: List[MedCandidate] = []
    for c in safe:
        key = (c.name or "").lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(c)

    return out
