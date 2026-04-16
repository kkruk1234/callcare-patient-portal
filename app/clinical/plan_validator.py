from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


ALLOWED_TYPES = {"yesno", "yesno_unknown", "multiple_choice", "free_text"}

# Conservative: block anything that looks like dx/tx/med advice in question prompts.
DISALLOWED_PATTERNS = [
    r"\bdiagnos",                 # diagnosis/diagnose
    r"\byou have\b",
    r"\btake\b",
    r"\bstart\b.*\bmed",
    r"\bprescrib",
    r"\brecommend\b.*\bmed",
    r"\bamoxicillin\b",
    r"\bazithro\b",
    r"\bdoxy\b",
    r"\bantibiotic\b.*\bfor you\b",
]

DEFAULT_MAX_QUESTIONS = 12


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    normalized_plan: Optional[Dict[str, Any]]
    reason: Optional[str]


def validate_and_normalize_plan(plan: Dict[str, Any], max_questions: int = DEFAULT_MAX_QUESTIONS) -> ValidationResult:
    if not isinstance(plan, dict):
        return ValidationResult(False, None, "Plan is not a JSON object.")

    domain = plan.get("domain")
    questions = plan.get("questions")
    required = plan.get("required_checks_included")

    if not isinstance(domain, str) or not domain.strip():
        return ValidationResult(False, None, "Missing/invalid 'domain'.")

    if not isinstance(questions, list) or not questions:
        return ValidationResult(False, None, "Missing/invalid 'questions' list.")

    if len(questions) > max_questions:
        return ValidationResult(False, None, f"Too many questions ({len(questions)} > {max_questions}).")

    if not isinstance(required, dict):
        return ValidationResult(False, None, "Missing/invalid 'required_checks_included' object.")

    for k in ["red_flags", "med_exclusions", "allergies", "pregnancy_possible"]:
        if k not in required or not isinstance(required[k], bool):
            return ValidationResult(False, None, f"Missing/invalid required_checks_included.{k}")

    seen_ids = set()
    seen_store_as = set()

    norm_questions: List[Dict[str, Any]] = []
    has_med_exclusions = False

    for q in questions:
        if not isinstance(q, dict):
            return ValidationResult(False, None, "Question item is not an object.")

        qid = q.get("id")
        prompt = q.get("prompt")
        qtype = q.get("type")
        store_as = q.get("store_as")
        rationale = q.get("rationale")
        evidence_keys = q.get("evidence_keys")
        choices = q.get("choices", None)

        if not isinstance(qid, str) or not qid.strip():
            return ValidationResult(False, None, "Question missing/invalid id.")
        if qid in seen_ids:
            return ValidationResult(False, None, f"Duplicate question id: {qid}")
        seen_ids.add(qid)

        if not isinstance(prompt, str) or not prompt.strip():
            return ValidationResult(False, None, f"Question {qid} missing/invalid prompt.")

        low = prompt.lower()
        for pat in DISALLOWED_PATTERNS:
            if re.search(pat, low):
                return ValidationResult(False, None, f"Disallowed content in prompt for {qid}.")

        if qtype not in ALLOWED_TYPES:
            return ValidationResult(False, None, f"Question {qid} has invalid type: {qtype}")

        if not isinstance(store_as, str) or not store_as.strip():
            return ValidationResult(False, None, f"Question {qid} missing/invalid store_as.")
        if store_as in seen_store_as:
            return ValidationResult(False, None, f"Duplicate store_as key: {store_as}")
        seen_store_as.add(store_as)

        if not isinstance(rationale, str) or not rationale.strip():
            return ValidationResult(False, None, f"Question {qid} missing/invalid rationale.")

        # Evidence keys required for RAG plan questions (universal gate is outside the plan)
        if not isinstance(evidence_keys, list) or len(evidence_keys) < 1 or not all(isinstance(x, str) and x.strip() for x in evidence_keys):
            return ValidationResult(False, None, f"Question {qid} missing/invalid evidence_keys.")

        if qtype == "multiple_choice":
            if not isinstance(choices, list) or len(choices) < 2 or not all(isinstance(x, str) and x.strip() for x in choices):
                return ValidationResult(False, None, f"Question {qid} multiple_choice missing/invalid choices.")
        else:
            choices = None

        if store_as == "med_exclusions":
            has_med_exclusions = True
            if qtype != "free_text":
                return ValidationResult(False, None, "med_exclusions must be free_text.")

        norm_questions.append({
            "id": qid.strip(),
            "prompt": prompt.strip(),
            "type": qtype,
            "choices": choices,
            "store_as": store_as.strip(),
            "rationale": rationale.strip(),
            "evidence_keys": evidence_keys,
        })

    if required.get("med_exclusions", False) and not has_med_exclusions:
        return ValidationResult(False, None, "required_checks_included.med_exclusions true but no med_exclusions question found.")

    normalized = {
        "domain": domain.strip(),
        "questions": norm_questions,
        "required_checks_included": {
            "red_flags": bool(required["red_flags"]),
            "med_exclusions": bool(required["med_exclusions"]),
            "allergies": bool(required["allergies"]),
            "pregnancy_possible": bool(required["pregnancy_possible"]),
        },
    }

    return ValidationResult(True, normalized, None)
