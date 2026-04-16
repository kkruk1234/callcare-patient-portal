from __future__ import annotations
from typing import Any, Dict, List, Optional
import json, re

from app.clinical.llm_adapter import llm_chat

def _compact_candidates(cands: List[Dict[str, Any]], limit: int = 80) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for c in (cands or [])[:limit]:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "").strip()[:180]
        url = str(c.get("url") or c.get("source") or "").strip()[:280]
        text = str(c.get("text") or "").strip()
        snippet = re.sub(r"\s+", " ", text)[:320]
        out.append({"title": title, "url": url, "snippet": snippet})
    return out

def _safe_json_extract(s: str) -> Optional[dict]:
    if not s:
        return None
    s = s.strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"(\{.*\})", s, flags=re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def build_evidence_and_plan_onecall(
    *,
    diagnosis: str,
    chief_complaint: str,
    age_years: Optional[int],
    pregnancy_possible: str,
    renal_disease: str,
    liver_disease: str,
    candidates: List[Dict[str, Any]],
    k: int = 6,
) -> Optional[Dict[str, Any]]:
    """
    One LLM call returns:
      - refined_queries: list[str]   (scalable query rewrite; no anatomy lists)
      - selected: list[{title,url,reason,category}]
      - plan_text: string            (telephone-only plan with meds + dosing + nonpharm + cautions)
      - need_auto_acquire: bool      (true if evidence pool is weak/off-topic)
    """
    compact = _compact_candidates(candidates, limit=80)

    system = (
        "You are an evidence triage + plan drafting assistant for a TELEPHONE-ONLY telemedicine system.\n"
        "You must do THREE things in ONE response:\n"
        "1) Rewrite better retrieval queries so evidence matches the diagnosis (scalable; no hardcoded anatomy lists).\n"
        "2) Select the best Evidence Used items from provided candidates.\n"
        "3) Draft the Plan (P:) using ONLY the selected evidence.\n\n"
        "HARD RULES:\n"
        "- Telephone-only: NEVER say 'return to clinic'. Use: 'seek in-person care', 'urgent care', 'emergency department', or 'in-person clinician' ONLY WHEN INDICATED.\n"
        "- Do NOT give blanket 'seek in-person care' to everyone. Instead give SPECIFIC CRITERIA + TIMING, e.g.:\n"
        "  * same-day / now (ED) for red flags\n"
        "  * urgent (24–48h) if worsening / new concerning features\n"
        "  * routine in-person evaluation if not improving after an evidence-consistent timeframe\n"
        "- Testing: assume NO labs/imaging available by phone. If testing is needed now per evidence -> advise in-person evaluation for testing.\n"
        "- Evidence relevance must be SCALABLE:\n"
        "  * Only select sources that meaningfully match the provided diagnosis/CC.\n"
        "  * Reject sources that are off-topic even if they are from trusted sites.\n"
        "  * Reject drug monographs unrelated to the clinical problem.\n"
        "  * Avoid library/content.txt chunks when a real condition/guideline page exists.\n\n"
        "PLAN REQUIREMENTS:\n"
        "- Include non-pharmacologic care if supported (e.g., ice/heat/activity/rest/stretching/ergonomics).\n"
        "- Include first-line AND alternatives medications when appropriate.\n"
        "- Include dosing when supported.\n"
        "- Include a concise 'Key cautions/contraindications' line for common/important exclusions relevant to proposed meds:\n"
        "  examples: renal disease/CKD, liver disease, pregnancy/breastfeeding, GI ulcer/bleed, anticoagulants, CV disease, asthma/NSAID sensitivity.\n"
        "  Keep it short (1–3 bullet lines), not a paragraph.\n"
        "- If evidence does NOT contain dosing, say 'Dosing details not available in retrieved sources' and keep medication suggestions general.\n\n"
        "Output JSON only."
    )

    user = {
        "diagnosis": diagnosis,
        "chief_complaint": chief_complaint,
        "patient_context": {
            "age_years": age_years,
            "pregnancy_possible": pregnancy_possible,
            "renal_disease": renal_disease,
            "liver_disease": liver_disease,
        },
        "candidates": compact,
        "instructions": {
            "choose_k": k,
            "categories_required": [
                "condition_management",
                "medications_and_dosing",
                "red_flags_or_referral"
            ],
            "output_schema": {
                "refined_queries": ["string"],
                "selected": [
                    {"title":"string","url":"string","reason":"string","category":"string"}
                ],
                "plan_text": "string",
                "need_auto_acquire": "boolean"
            }
        }
    }

    raw = llm_chat(system, json.dumps(user, ensure_ascii=False), temperature=0.15, max_tokens=1200)
    if not raw:
        return None

    obj = _safe_json_extract(raw)
    if not obj:
        return None

    if not isinstance(obj.get("selected"), list):
        return None
    if not isinstance(obj.get("plan_text"), str):
        return None
    if not isinstance(obj.get("refined_queries"), list):
        obj["refined_queries"] = []

    return obj
