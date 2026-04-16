from __future__ import annotations

from typing import List, Dict, Any

from app.core.models import Decision, EvidenceRef, CallState
from app.rag.retrieve import retrieve


# ----------------------------
# Helpers
# ----------------------------

def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("yes", "true", "y", "1")


def _norm(s: Any) -> str:
    return ("" if s is None else str(s)).strip().lower()


def _sx(state: CallState) -> Dict[str, Any]:
    sx = getattr(state, "symptoms", None) or {}
    return sx if isinstance(sx, dict) else {}


def _pid(state: CallState) -> str:
    sx = _sx(state)
    pw = sx.get("_pathway") or {}
    if isinstance(pw, dict) and pw.get("id"):
        return str(pw["id"]).strip()
    pid = getattr(state, "pathway_id", None) or getattr(state, "pathway", None)
    return (str(pid).strip() if pid else "")


def _answers(state: CallState) -> Dict[str, Any]:
    a = _sx(state).get("_answers") or {}
    return a if isinstance(a, dict) else {}


def _has_any(text: Any, needles: List[str]) -> bool:
    hay = _norm(text)
    if not hay:
        return False
    return any(n in hay for n in needles)


def _build_query(state: CallState) -> str:
    sx = _sx(state)
    pid = _pid(state)
    cc = (getattr(state, "chief_complaint", "") or "").strip()

    parts: List[str] = []
    if pid:
        parts.append(pid.replace("_", " "))
    if cc:
        parts.append(cc)

    for k in (
        "duration",
        "duration_days",
        "age_years",
        "sex_at_birth",
        "pregnancy_possible",
        "location",
        "unilateral_swelling",
        "dvt_symptoms",
        "dvt_risk_factors_recent",
        "dvt_risk_factors_additional",
        "pe_symptoms",
        "limb_infection_signs",
        "limb_context_signs",
        "dvt_risk_factors_recent",
        "dvt_risk_factors_medical",
    ):
        if k in sx and sx.get(k) not in (None, ""):
            parts.append(f"{k}:{sx.get(k)}")

    ans = _answers(state)
    if ans:
        yn_bits = []
        for k, v in list(ans.items())[:25]:
            vv = _norm(v)
            if vv in ("yes", "no"):
                yn_bits.append(f"{k}:{vv}")
        if yn_bits:
            parts.append("answers " + " ".join(yn_bits[:15]))

    return " ".join(parts).strip()


def _evidence_from_retrieval(query: str, k: int = 6) -> List[EvidenceRef]:
    try:
        results = retrieve(query, k=k) or []
    except Exception:
        results = []

    evidence: List[EvidenceRef] = []
    for r in results[:8]:
        try:
            src = (r.get("source") or "").strip()
            title = (r.get("title") or "Guideline excerpt").strip()
            url = (r.get("url") or "").strip()
            text = (r.get("text") or "").strip()
            evidence.append(EvidenceRef(source=src, title=title, url=url, snippet=text[:400]))
        except Exception:
            continue
    return evidence


# ----------------------------
# DVT/PE interpretation (grouped answers)
# ----------------------------

def _pe_symptoms_present(sx: Dict[str, Any]) -> bool:
    pe = sx.get("pe_symptoms", "")
    return _has_any(pe, ["shortness of breath", "short of breath", "sob", "breath"]) or _has_any(pe, ["chest pain"])


def _dvt_risk_present(sx: Dict[str, Any]) -> bool:
    rf = " ".join([str(sx.get("dvt_risk_factors_recent", "")), str(sx.get("dvt_risk_factors_additional", "")),
                  str(sx.get("dvt_risk_factors_medical", ""))])
    return _has_any(
        rf,
        [
            "surgery", "hospital", "hospitalization",
            "travel", "flight", "car", "long travel",
            "immobil", "bedbound", "cast", "splint",
            "cancer",
            "prior dvt", "prior pe", "dvt", "pe", "blood clot", "clot",
            "estrogen", "birth control", "hrt", "hormone",
            "pregnan", "postpartum",
        ],
    )


def _dvt_symptoms_present(sx: Dict[str, Any]) -> bool:
    # For dvt_possible.yaml
    s = sx.get("dvt_symptoms", "")
    if _has_any(s, ["calf pain", "calf tenderness", "calf"]):
        return True
    # For cellulitis.yaml variants
    s2 = sx.get("limb_context_signs", "")
    return _has_any(s2, ["calf pain", "calf tenderness", "calf"])


def _dvt_cannot_exclude(sx: Dict[str, Any]) -> bool:
    unilateral = _norm(sx.get("unilateral_swelling"))
    unilateral_yes = unilateral in ("yes", "y", "true", "1")
    if not unilateral_yes:
        return False
    return _dvt_symptoms_present(sx) or _dvt_risk_present(sx)


# ----------------------------
# Disposition enforcement
# ----------------------------

def _enforce_disposition(pid: str, sx: Dict[str, Any]) -> str:
    pid = (pid or "").strip()

    # DVT pathway: treat as DVT until proven otherwise
    if pid in ("dvt_possible",):
        if _pe_symptoms_present(sx):
            return "ed_now"
        return "urgent_today"

    # Existing cellulitis bucket: if DVT cannot be excluded, override + suppress meds
    if pid in ("cellulitis", "wound_infection_possible", "skin_abscess"):
        if _pe_symptoms_present(sx):
            sx["_suppress_meds"] = True
            return "ed_now"
        if _dvt_cannot_exclude(sx):
            sx["_suppress_meds"] = True
            return "urgent_today"

        # If not DVT-like, keep urgent bias for cellulitis
        return "urgent_today"

    # ---- (rest of your original enforcement logic unchanged) ----

    if pid in ("stroke_neuro",):
        onset = _bool(sx.get("sudden_recent_onset"))
        face = _bool(sx.get("face_droop"))
        arm = _bool(sx.get("one_sided_weakness"))
        speech = _bool(sx.get("speech_change"))
        if onset and (face or arm or speech):
            return "ed_now"
        return "urgent_today"

    if pid in ("chest_pain",):
        if _bool(sx.get("severe_breathing")) or _bool(sx.get("syncope")):
            return "ed_now"
        if _bool(sx.get("high_risk_associated")) and (_bool(sx.get("radiation")) or _bool(sx.get("exertional"))):
            return "ed_now"
        return "urgent_today"

    if pid in ("appendicitis_possible",):
        if _bool(sx.get("severe")) or _bool(sx.get("peritoneal_like")) or _bool(sx.get("vomiting")):
            return "ed_now"
        return "urgent_today"

    if pid in ("pyelonephritis_possible", "kidney_infection_possible"):
        if (_bool(sx.get("fever")) and _bool(sx.get("flank_pain"))) or _bool(sx.get("vomiting")) or _bool(sx.get("dangerously_ill")):
            return "ed_now"
        return "urgent_today"

    if pid in ("gi_bleed_possible",):
        if _bool(sx.get("hemodynamic_symptoms")) or _bool(sx.get("hematemesis")) or _bool(sx.get("melena_or_large_bleed")):
            return "ed_now"
        if _bool(sx.get("large_or_melena")) or _bool(sx.get("shock_signs")) or _bool(sx.get("anticoagulant")):
            return "ed_now"
        return "urgent_today"

    if pid in ("sepsis_possible", "meningitis_possible", "anaphylaxis_possible"):
        if _bool(sx.get("altered")) or _bool(sx.get("faint")) or _bool(sx.get("breathing_trouble")):
            return "ed_now"
        if pid == "meningitis_possible" and _bool(sx.get("fever")) and _bool(sx.get("stiff_neck")):
            return "ed_now"
        if pid == "anaphylaxis_possible" and (_bool(sx.get("airway")) or _bool(sx.get("faint"))):
            return "ed_now"
        return "ed_now"

    if pid in ("testicular_pain_torsion_possible",):
        if _bool(sx.get("sudden_onset")) or _bool(sx.get("swelling_or_high")) or _bool(sx.get("nausea_vomiting")):
            return "ed_now"
        return "ed_now"

    if pid in ("acute_back_pain_redflags",):
        if _bool(sx.get("bladder_issue")) or _bool(sx.get("saddle_numb")) or _bool(sx.get("leg_weakness")):
            return "ed_now"
        return "urgent_today"

    if pid in ("acute_abd_pain_redflags",):
        if _bool(sx.get("faint")) or _bool(sx.get("gi_bleed")) or _bool(sx.get("severe")):
            return "ed_now"
        return "urgent_today"

    if pid in ("headache_redflags",):
        if _bool(sx.get("thunderclap")) or _bool(sx.get("neuro_deficit")) or _bool(sx.get("fever_or_stiff")):
            return "ed_now"
        return "urgent_today"

    if pid in ("eye_floaters_flashes",):
        if _bool(sx.get("curtain")) or _bool(sx.get("vision_loss")):
            return "ed_now"
        if _bool(sx.get("severe_pain_photophobia")):
            return "urgent_today"
        return "urgent_today"

    if pid in ("eye_foreign_body",):
        if _bool(sx.get("chemical")):
            return "ed_now"
        if _bool(sx.get("vision_change")) or _bool(sx.get("severe_pain")):
            return "urgent_today"
        return "urgent_today"

    if pid in ("acute_angle_closure_glaucoma_possible",):
        if _bool(sx.get("vision_loss")) or _bool(sx.get("nausea_vomiting")) or _bool(sx.get("severe_pain")):
            return "ed_now"
        if _bool(sx.get("halos")) or _bool(sx.get("severe_headache")):
            return "urgent_today"
        return "urgent_today"

    if pid in ("pneumonia_possible", "pneumonia_adult_possible", "pneumonia_child_possible", "pneumonia_child_possible"):
        if _bool(sx.get("breathing_trouble")) or _bool(sx.get("chest_pain")) or _bool(sx.get("sob_severe")) or _bool(sx.get("danger_signs")):
            return "ed_now"
        return "urgent_today"

    if pid in ("asthma_wheeze", "asthma_child_wheeze", "copd_exacerbation"):
        if _bool(sx.get("severe_work_of_breathing")) or _bool(sx.get("cyanosis")) or _bool(sx.get("severe_respiratory")):
            return "ed_now"
        if _bool(sx.get("rescue_not_helping")) or _bool(sx.get("severe_breathing")):
            return "ed_now"
        return "urgent_today"

    if pid in ("otitis_externa",):
        diab = _norm(sx.get("diabetes_immuno")) in ("yes", "true", "y")
        if _bool(sx.get("severe_pain_or_hearing")) or _bool(sx.get("fever")) or diab:
            return "urgent_today"
        return "routine"

    if pid in ("dental_abscess",):
        if _bool(sx.get("airway_or_swallow")) or _bool(sx.get("swallow_or_trismus")):
            return "ed_now"
        return "urgent_today"

    if pid in ("tick_bite", "tick_borne_illness_possible"):
        if _bool(sx.get("systemic_symptoms")) and _bool(sx.get("expanding_rash")):
            return "urgent_today"
        if _bool(sx.get("systemic_symptoms")) or _bool(sx.get("expanding_rash")) or _bool(sx.get("bullseye_rash")) or _bool(sx.get("erythema_migrans")):
            return "urgent_today"
        return "routine"

    if pid in ("bacterial_sinusitis_possible", "sinusitis", "uri_sinus_overlap", "viral_uri_adult", "cough_uri"):
        if _bool(sx.get("orbital_or_neuro")) or _bool(sx.get("orbital_or_focal")):
            return "ed_now"
        if _bool(sx.get("severe_pain_or_fever")) or _bool(sx.get("double_worsening")) or _bool(sx.get("severe_redflags")):
            return "urgent_today"
        return "routine"

    if pid in ("uti_male",):
        if _bool(sx.get("fever")) or _bool(sx.get("flank_pain")) or _bool(sx.get("testicular_pain")):
            return "urgent_today"
        return "urgent_today"

    if pid in ("uti_uncomplicated", "dysuria_uti"):
        if _bool(sx.get("fever")) or _bool(sx.get("flank_pain")) or _norm(sx.get("pregnancy_possible")) in ("yes", "true", "y", "unknown"):
            return "urgent_today"
        return "urgent_today"

    if pid in ("uti_complicated", "uti_pregnancy_possible"):
        if _bool(sx.get("fever")) and _bool(sx.get("flank_pain")):
            return "ed_now"
        return "urgent_today"

    return "routine"


# ----------------------------
# Primary decide() entrypoint
# ----------------------------

def decide(state: CallState) -> Decision:
    pid = _pid(state)
    sx = _sx(state)

    query = _build_query(state)
    evidence = _evidence_from_retrieval(query, k=6)

    disposition = _enforce_disposition(pid, sx)

    cc = (getattr(state, "chief_complaint", "") or "").strip()
    differential: List[str] = []

    # Working diagnosis anchor logic
    if pid == "dvt_possible":
        differential.append("deep vein thrombosis (DVT) possible")
    elif pid == "cellulitis" and _dvt_cannot_exclude(sx):
        differential.append("deep vein thrombosis (DVT) possible")

    if pid:
        differential.append(pid.replace("_", " "))
    if cc and (not pid or cc.lower() not in pid.replace("_", " ").lower()):
        differential.append(cc)

    plan = ["Async physician review will determine if prescriptions, testing, or in-person evaluation are needed."]
    safety_net = ["Seek urgent evaluation if symptoms worsen, new red flags develop, or you feel severely ill."]

    return Decision(
        disposition=disposition,
        differential=differential or [cc or "undifferentiated complaint"],
        plan=plan,
        safety_net=safety_net,
        evidence=evidence,
        uncertainty="Phone-only assessment; disposition is guideline-informed and intended for async physician review.",
    )
