from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _as_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        return str(x)
    except Exception:
        return ""


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _truthy_unknown(val: Any) -> str:
    s = _norm(_as_str(val))
    if s in {"yes", "y", "true", "t", "1"}:
        return "yes"
    if s in {"no", "n", "false", "f", "0"}:
        return "no"
    if s in {"unknown", "unsure", "not sure", "na", "n/a", ""}:
        return "unknown"
    return "unknown"


def _extract_evidence_urls(evidence: Any) -> List[str]:
    urls: List[str] = []
    if not evidence:
        return urls
    items = evidence if isinstance(evidence, list) else [evidence]
    for e in items:
        url = ""
        if isinstance(e, dict):
            url = _as_str(e.get("url") or e.get("source") or e.get("href") or "")
        else:
            url = _as_str(_get_attr(e, "url", "")) or _as_str(_get_attr(e, "source", ""))
        url = url.strip()
        if url.startswith("http://") or url.startswith("https://"):
            urls.append(url)
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@dataclass
class GatingFlags:
    pregnancy: str
    renal_disease: str
    liver_disease: str
    allergies_text: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pregnancy": self.pregnancy,
            "renal_disease": self.renal_disease,
            "liver_disease": self.liver_disease,
            "allergies_text": self.allergies_text,
        }


def _extract_gating_flags(state: Any) -> GatingFlags:
    preg = _truthy_unknown(_get_attr(state, "pregnancy_possible", None))
    sx = _get_attr(state, "symptoms", {}) or {}
    if not isinstance(sx, dict):
        sx = {}
    renal = _truthy_unknown(sx.get("renal_disease") or sx.get("_renal_disease") or sx.get("kidney_disease"))
    liver = _truthy_unknown(sx.get("liver_disease") or sx.get("_liver_disease"))
    allergies_raw = sx.get("allergies") or sx.get("_allergies") or _get_attr(state, "allergies", "") or ""
    return GatingFlags(
        pregnancy=preg,
        renal_disease=renal,
        liver_disease=liver,
        allergies_text=_as_str(allergies_raw).strip(),
    )


def _allergy_hits(allergies_text: str) -> Dict[str, bool]:
    t = _norm(allergies_text)
    return {
        "penicillin": ("penicillin" in t) or ("pcn" in t),
        "cephalosporin": ("ceph" in t) or ("cephal" in t) or ("cef" in t),
        "sulfa": ("sulfa" in t) or ("sulfonamide" in t) or ("tmp-smx" in t) or ("bactrim" in t),
        "macrolide": ("azithro" in t) or ("azithromycin" in t) or ("clarithro" in t) or ("erythro" in t),
        "tetracycline": ("doxy" in t) or ("doxycycline" in t) or ("tetracycline" in t) or ("minocycline" in t),
        "fluoroquinolone": ("cipro" in t) or ("ciprofloxacin" in t) or ("levo" in t) or ("levofloxacin" in t) or ("moxi" in t) or ("moxifloxacin" in t),
    }


def _default_testing_lines() -> List[str]:
    return [
        "If no improvement within 48–72 hours, worsening symptoms, or recurrent symptoms: seek in-person evaluation for appropriate testing (e.g., urinalysis/urine culture or other workup as indicated)."
    ]


def _default_return_precautions() -> List[str]:
    return [
        "Go to urgent care/ED now for severe or rapidly worsening symptoms, trouble breathing, chest pain, confusion, fainting, or inability to keep fluids down.",
        "Seek urgent in-person care for new high fever, severe pain, dehydration concerns, or any red-flag symptoms discussed during the call.",
    ]


def generate_med_support(
    *,
    state: Any,
    evidence: Any,
    working_syndrome: Optional[str] = None,
) -> Dict[str, Any]:
    gating = _extract_gating_flags(state)
    allergy_flags = _allergy_hits(gating.allergies_text)
    evidence_urls = _extract_evidence_urls(evidence)

    # Deterministic syndrome signal if present
    if working_syndrome is None:
        sx = _get_attr(state, "symptoms", {}) or {}
        if isinstance(sx, dict):
            working_syndrome = sx.get("_working_syndrome") or sx.get("working_syndrome")
        if not working_syndrome:
            working_syndrome = _get_attr(state, "decision", None)
    working_syndrome = _as_str(working_syndrome).strip() or None

    plan_assessment: List[str] = []
    if working_syndrome:
        plan_assessment.append(
            f"Working impression: {working_syndrome}. Management below is limited by telephone-only evaluation (no vitals/labs available during the call)."
        )
    else:
        plan_assessment.append(
            "Working impression based on telephone history. Management below is limited by telephone-only evaluation (no vitals/labs available during the call)."
        )

    # Phase 1: conservative defaults
    testing_lines = _default_testing_lines()
    return_precautions = _default_return_precautions()

    gating_notes: List[str] = []
    if gating.pregnancy != "no":
        gating_notes.append("pregnancy status is yes/unknown (avoid teratogenic options unless explicitly appropriate)")
    if gating.renal_disease != "no":
        gating_notes.append("renal disease status is yes/unknown (dose adjustment / avoid certain meds)")
    if gating.liver_disease != "no":
        gating_notes.append("liver disease status is yes/unknown (avoid hepatotoxic meds / adjust dosing)")
    if gating.allergies_text:
        gating_notes.append(f"reported allergies: {gating.allergies_text}")

    # Phase 1: structured scaffold; no dosing
    rx_candidates: List[Dict[str, Any]] = [
        {
            "drug": "Supportive care",
            "class": "non-prescription",
            "intent": "symptom relief and supportive management",
            "blocked": False,
            "block_reasons": [],
            "allergy_flags": allergy_flags,
            "gating_notes": gating_notes,
            "evidence_urls": evidence_urls,
        }
    ]

    return {
        "plan_assessment": plan_assessment,
        "rx_candidates": rx_candidates,
        "gating_flags": {**gating.as_dict(), "allergy_flags": allergy_flags},
        "return_precautions": return_precautions,
        "testing_lines": testing_lines,
    }
