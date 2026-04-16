from __future__ import annotations

from typing import Any, Dict


def build_generic_fallback_plan() -> Dict[str, Any]:
    """
    Deterministic minimal safe plan when RAG/planner/validator fails.
    Evidence keys are empty because this is not retrieval-grounded.
    Note writer must avoid medical claims without evidence.
    """
    return {
        "domain": "generic urgent care intake",
        "questions": [
            {
                "id": "symptom_onset",
                "prompt": "When did this start?",
                "type": "free_text",
                "choices": None,
                "store_as": "symptom_onset",
                "rationale": "Timeline helps assess acuity.",
                "evidence_keys": [],
            },
            {
                "id": "severity",
                "prompt": "How severe is it right now?",
                "type": "multiple_choice",
                "choices": ["mild", "moderate", "severe"],
                "store_as": "severity",
                "rationale": "Severity helps decide urgency.",
                "evidence_keys": [],
            },
            {
                "id": "main_symptoms",
                "prompt": "Tell me the main symptoms you're having.",
                "type": "free_text",
                "choices": None,
                "store_as": "main_symptoms",
                "rationale": "Captures the symptom cluster.",
                "evidence_keys": [],
            },
            {
                "id": "red_flags_specific",
                "prompt": "Any severe trouble breathing, chest pressure, fainting, new weakness/numbness on one side, confusion, or uncontrolled bleeding?",
                "type": "yesno",
                "choices": None,
                "store_as": "red_flags_specific",
                "rationale": "Screens for urgent/emergent conditions.",
                "evidence_keys": [],
            },
            {
                "id": "med_exclusions",
                "prompt": "Any kidney disease, liver disease, heart rhythm or QT problems (or QT-prolonging meds), blood thinners, or history of ulcers or GI bleeding?",
                "type": "free_text",
                "choices": None,
                "store_as": "med_exclusions",
                "rationale": "Medication safety screen.",
                "evidence_keys": [],
            },
        ],
        "required_checks_included": {
            "red_flags": True,
            "med_exclusions": True,
            "allergies": True,
            "pregnancy_possible": True,
        },
        "fallback_mode": True,
    }
