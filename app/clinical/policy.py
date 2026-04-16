from enum import Enum
from dataclasses import dataclass
from typing import Optional
from app.core.models import CallState


class AutonomyTier(str, Enum):
    TIER_1_PROTOCOL = "TIER_1_PROTOCOL"  # Utah-equivalent autonomy
    TIER_2_REVIEW = "TIER_2_REVIEW"      # Expanded scope w/ async physician review


@dataclass
class PolicyResult:
    tier: AutonomyTier
    reason: str
    protocol_name: Optional[str] = None


def classify_encounter(state: CallState) -> PolicyResult:
    """
    Very first classifier: detects refill/renewal requests (Tier 1) vs everything else (Tier 2).
    We'll refine this later with explicit encounter types.
    """
    cc = (state.chief_complaint or "").lower()

    refill_keywords = ["refill", "renew", "renewal", "med refill", "medication refill", "prescription refill"]
    if any(k in cc for k in refill_keywords):
        return PolicyResult(
            tier=AutonomyTier.TIER_1_PROTOCOL,
            reason="Medication renewal/refill workflow",
            protocol_name="MED_RENEWAL_V1",
        )

    return PolicyResult(
        tier=AutonomyTier.TIER_2_REVIEW,
        reason="Not a Tier-1 protocol encounter",
        protocol_name=None,
    )
