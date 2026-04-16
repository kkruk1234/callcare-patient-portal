from __future__ import annotations
from typing import Any, Dict, List, Optional
import json
import re

from app.clinical.llm_adapter import llm_chat


def _safe_json_extract(s: str) -> Optional[dict]:
    if not s:
        return None
    s = s.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"(\{.*\})", s, flags=re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def write_plan_llm(
    *,
    diagnosis: str,
    differential: str,
    chief_complaint: str,
    age_years: Optional[int],
    pregnancy_possible: str,
    renal_disease: str,
    liver_disease: str,
    evidence_selected: List[Dict[str, str]],
) -> Optional[str]:
    """
    Returns the Plan (P:) content as a string (bulleted), or None if LLM unavailable.
    """
    system = (
        "You are drafting the PLAN (P:) section of a physician note for a telephone-only telemedicine visit.\n"
        "Write as the treating clinician.\n"
        "Do NOT mention any product/system name.\n"
        "\n"
        "EVIDENCE USE (NON-NEGOTIABLE):\n"
        "- Use ONLY the provided evidence (including snippets, titles, and URLs) as your basis.\n"
        "- Evidence snippets are authoritative. Do NOT contradict them.\n"
        "- If an evidence snippet uses hierarchy language such as \"primary recommended\", \"first-line\", "
        "\"recommended treatment\", \"preferred\", or \"should be offered\", then your plan MUST reflect that ordering.\n"
        "\n"
        "FIRST-LINE RULE (TWO-LEVEL):\n"
        "- Default: list ONE specific medication (a proper name) as first-line.\n"
        "- Exception: if the evidence explicitly recommends only a class/modality as first line treatment and does not name a specific agent, "
        "you may list the class/modality as first-line and indicate it is per evidence.\n"
        "\n"
        "DOSING RULE:\n"
        "- If you include medication dosing, it must be typical outpatient dosing and must reflect pregnancy/renal/liver constraints.\n"
        "- If dosing is NOT clearly supported by the provided evidence, append: \"(Physician must verify medication/dosing)\"\n"
        "- If dosing IS supported by evidence snippets, do NOT include citations, evidence IDs, or source references.\n"
        "\n"
        "OUTPUT JSON ONLY with key plan_text.\n"
        "Plan must include these headings in order:\n"
        "- Treatment:\n"
        "- Testing:\n"
        "- Follow-up recommendations:\n"
        "Under Follow-up recommendations, use phrasing like: \"Seek immediate in-person care if …\" (not \"Return if\").\n"
    )

    payload = {
        "diagnosis": diagnosis,
        "differential": differential,
        "chief_complaint": chief_complaint,
        "patient_context": {
            "age_years": age_years,
            "pregnancy_possible": pregnancy_possible,
            "renal_disease": renal_disease,
            "liver_disease": liver_disease,
        },
        "evidence_selected": evidence_selected,
        "output_schema": {"plan_text": "string"}
    }

    raw = llm_chat(system, json.dumps(payload, ensure_ascii=False), temperature=0.2, max_tokens=900)
    if not raw:
        return None

    obj = _safe_json_extract(raw)
    if not obj:
        return None

    txt = obj.get("plan_text")
    if isinstance(txt, str) and txt.strip():
        return txt.strip()
    return None
