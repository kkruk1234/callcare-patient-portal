from typing import List
from app.core.models import CallState


def renewal_eligible(state: CallState) -> (bool, List[str]):
    """
    Protocol-style exclusion criteria (Utah-equivalent pattern):
    - Only renewals of previously prescribed meds (patient already taking)
    - Excludes red flags / concerning symptoms / pregnancy risk (simplified)
    """
    reasons = []

    med = (state.symptoms.get("med_name") or "").strip()
    if not med:
        reasons.append("Missing medication name")

    previously_prescribed = (state.symptoms.get("previously_prescribed") or "").strip().lower()
    if previously_prescribed not in {"yes", "no"}:
        reasons.append("Need confirmation medication was previously prescribed")
    elif previously_prescribed == "no":
        reasons.append("Not previously prescribed")

    new_symptoms = (state.symptoms.get("new_or_worse_symptoms") or "").strip().lower()
    if new_symptoms == "yes":
        reasons.append("New or worsening symptoms")

    side_effects = (state.symptoms.get("side_effects_concern") or "").strip().lower()
    if side_effects == "yes":
        reasons.append("Side effects concern")

    # If any emergency red flags were marked present earlier, not eligible.
    if any(rf.present for rf in (state.red_flags or [])):
        reasons.append("Red flags present")

    eligible = len(reasons) == 0
    return eligible, reasons
