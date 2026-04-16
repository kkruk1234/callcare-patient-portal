from typing import List
from app.core.models import CallState, EvidenceRef, NoteDraft
from app.rag.retrieve import retrieve


def _dedup_results(results):
    seen = set()
    out = []
    for r in results:
        key = (r["source"], r["text"][:120])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def build_evidence_for_med_renewal(state: CallState) -> List[EvidenceRef]:
    med = (state.symptoms.get("med_name") or "").strip()
    # Force a renewal-specific query so we don't retrieve URI/UTI chunks.
    query = f"medication renewal refill protocol eligibility screening {med}".strip()

    results = retrieve(query, k=10)
    results = _dedup_results(results)

    # Tier-1 evidence MUST come from the renewal protocol source
    results = [r for r in results if "protocol_med_renewal_demo.txt" in r["source"]]

    evidence = []
    for r in results[:3]:
        evidence.append(
            EvidenceRef(
                source=r["source"],
                title="Renewal protocol excerpt",
                snippet=r["text"][:350],
            )
        )
    return evidence


def build_note_med_renewal_final(state: CallState) -> NoteDraft:
    med = state.symptoms.get("med_name") or "Not stated"
    prev = state.symptoms.get("previously_prescribed") or "unknown"
    sidefx = state.symptoms.get("side_effects_concern") or "unknown"
    newsx = state.symptoms.get("new_or_worse_symptoms") or "unknown"

    evidence = build_evidence_for_med_renewal(state)

    soap = (
        "S: Medication renewal request. "
        f"Medication: {med}. Previously prescribed: {prev}. "
        f"Side effects concern: {sidefx}. New/worsening symptoms: {newsx}. "
        f"Age band: {state.age_band or 'Not stated'}.\n"
        "O: Phone encounter. No vitals available.\n"
        "A: Medication renewal - protocol-eligible (Tier 1) pending final order entry.\n"
        "P: Approve renewal per protocol constraints; if any exclusion criteria present, route to clinician review.\n"
    )

    patient_instructions = [
        "If you develop new or worsening symptoms, concerning side effects, chest pain, or severe trouble breathing, seek urgent care or call 911.",
        "If your condition changes, contact a clinician for reassessment.",
    ]

    clinician_questions = []  # Tier-1 should not require questions by default
    risk_flags = []

    return NoteDraft(
        soap=soap,
        patient_instructions=patient_instructions,
        clinician_questions=clinician_questions,
        risk_flags=risk_flags,
        evidence=evidence,
    )
