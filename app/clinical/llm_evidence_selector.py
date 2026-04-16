from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import json
import re

from app.clinical.llm_adapter import llm_chat

def _compact_candidates(cands: List[Dict[str, Any]], limit: int = 40) -> List[Dict[str, str]]:
    """
    Keep only what LLM needs, keep it small to stay fast.
    """
    out = []
    for c in (cands or [])[:limit]:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "").strip()[:140]
        url = str(c.get("url") or c.get("source") or "").strip()[:240]
        text = str(c.get("text") or "").strip()
        snippet = re.sub(r"\s+", " ", text)[:260]
        if not title and not url and not snippet:
            continue
        out.append({"title": title, "url": url, "snippet": snippet})
    return out

def _safe_json_extract(s: str) -> Optional[dict]:
    """
    Extract first JSON object in a possibly messy LLM response.
    """
    if not s:
        return None
    s = s.strip()
    # If response is pure JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # Otherwise attempt to find JSON block
    m = re.search(r"(\{.*\})", s, flags=re.S)
    if not m:
        return None
    blob = m.group(1)
    try:
        obj = json.loads(blob)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None

def select_evidence_llm(
    *,
    diagnosis: str,
    chief_complaint: str,
    age_years: Optional[int],
    pregnancy_possible: str,
    renal_disease: str,
    liver_disease: str,
    candidates: List[Dict[str, Any]],
    k: int = 6,
) -> Optional[List[Dict[str, str]]]:
    """
    Returns a list of chosen evidence items: [{"title","url","reason","category"}]
    or None if no LLM available.
    """
    compact = _compact_candidates(candidates, limit=50)

    system = (
        "You are an evidence triage assistant for a physician-facing telemedicine note.\n"
        "Task: select the most relevant, condition-appropriate sources.\n"
        "Hard rules:\n"
        "- Never select irrelevant drug monographs (e.g., oncology injections) unless the diagnosis explicitly requires that drug.\n"
        "- Prefer outpatient management guidelines and condition pages. Prefer sources that mention treatment.\n"
        "- If location suggests chest wall/rib/costochondral pain, do NOT select sciatica.\n"
        "- Avoid generic 'content.txt' or uninformative chunk titles when better sources exist.\n"
        "- Output JSON only."
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
                "selected": [
                    {"title": "string", "url": "string", "reason": "string", "category": "string"}
                ]
            }
        }
    }

    raw = llm_chat(system, json.dumps(user, ensure_ascii=False), temperature=0.1, max_tokens=900)
    if not raw:
        return None

    obj = _safe_json_extract(raw)
    if not obj:
        return None

    selected = obj.get("selected")
    if not isinstance(selected, list):
        return None

    out = []
    for it in selected:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        reason = str(it.get("reason") or "").strip()
        cat = str(it.get("category") or "").strip()
        if not title and not url:
            continue
        out.append({"title": title, "url": url, "reason": reason, "category": cat})

    # enforce cap
    return out[:k] if out else None
