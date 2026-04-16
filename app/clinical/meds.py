
from __future__ import annotations

from typing import Dict, List, Any

from app.core.models import CallState


def _yn(val: Any) -> str:
    if isinstance(val, bool):
        return "yes" if val else "no"
    s = str(val or "").strip().lower()
    if s in ("yes", "y", "true", "1"):
        return "yes"
    if s in ("no", "n", "false", "0"):
        return "no"
    return s or "unknown"


def suggest_meds_tier1(state: CallState) -> Dict[str, List[str]]:
    """
    Tier-1 medication suggestions:
    - OTC options are allowed to mention to the patient (NO dosing).
    - Rx options are for the clinician reviewer only (AI does NOT prescribe).
    - Includes cautions + reviewer questions.
    """
    pw = (state.symptoms.get("_pathway") or {})
    pathway_id = str(pw.get("id") or "").strip()

    flags = getattr(state, "decision", None)
    # We also store flags in symptoms sometimes; be robust:
    # (You printed flags earlier from decision_engine output.)
    # We'll derive from state.symptoms if available.
    # If your decision engine stores flags elsewhere, this still works: it's optional.
    rf = state.symptoms.get("_flags", {}) if isinstance(state.symptoms.get("_flags", {}), dict) else {}

    otc: List[str] = []
    rx_review_only: List[str] = []
    cautions: List[str] = []
    reviewer_qs: List[str] = []

    # Global safe OTC baseline for pain/fever
    def add_basic_analgesics():
        if "acetaminophen (Tylenol)" not in otc:
            otc.append("acetaminophen (Tylenol) if you can take it safely (no dosing provided)")
        if "ibuprofen (Advil) if you can take it safely (no dosing provided)" not in otc:
            otc.append("ibuprofen (Advil) if you can take it safely (no dosing provided)")
        if "Avoid NSAIDs if you have kidney disease, a history of stomach bleeding/ulcers, or are on blood thinners (ask clinician)." not in cautions:
            cautions.append("Avoid NSAIDs if kidney disease, stomach ulcers/bleeding history, or on blood thinners (reviewer to confirm).")

    # --- Pathway-specific logic ---
    if pathway_id in ("dysuria_uti",):
        add_basic_analgesics()
        # Optional urinary analgesic (phone-safe with cautions; no dosing)
        otc.append("phenazopyridine (AZO) may help urinary burning; turns urine orange; does not treat infection (no dosing provided)")
        cautions.append("Avoid phenazopyridine if pregnant; reviewer to confirm pregnancy status.")
        reviewer_qs.extend([
            "Confirm pregnancy status; if pregnant or possibly pregnant, tailor evaluation/treatment.",
            "Any antibiotic allergies?",
            "Any flank pain/fever suggesting pyelonephritis (would need urgent in-person evaluation)?",
            "Any male patient, immunocompromised, or recurrent UTI (may need different workup)?",
        ])
        rx_review_only.extend([
            "If uncomplicated UTI likely and no red flags: consider empiric antibiotic per guideline (reviewer selects agent/dose/duration).",
        ])

    elif pathway_id in ("headache",):
        add_basic_analgesics()
        cautions.append("Avoid ibuprofen/NSAIDs if on anticoagulants or with kidney disease; reviewer to confirm.")
        reviewer_qs.append("Any thunderclap onset, neuro deficits, meningismus, or pregnancy/postpartum red flags?")

    elif pathway_id in ("sore_throat",):
        add_basic_analgesics()
        otc.append("warm salt-water gargles and honey (if age-appropriate) for throat comfort")
        reviewer_qs.append("If Centor/criteria suggest strep, consider testing/treatment per guideline (reviewer).")
        rx_review_only.append("If high suspicion for strep and appropriate: consider antibiotic per guideline (reviewer selects).")

    elif pathway_id in ("cough_uri", "viral_uri_adult", "bronchitis_uncomplicated", "sinusitis"):
        add_basic_analgesics()
        otc.append("saline nasal spray/irrigation for congestion (if safe)")
        otc.append("honey for cough (NOT for infants under 1 year)")
        cautions.append("Avoid honey in children under 12 months.")
        reviewer_qs.append("If shortness of breath, chest pain, or hypoxia concern: needs in-person evaluation.")
        if pathway_id == "sinusitis":
            rx_review_only.append("If bacterial sinusitis criteria met: consider antibiotic per guideline (reviewer selects).")

    elif pathway_id in ("diarrhea", "nausea_vomiting", "vomiting_child", "dehydration_mild"):
        otc.append("oral rehydration solution (ORS) / electrolyte fluids if able to drink")
        cautions.append("Seek urgent care if unable to keep fluids down or signs of dehydration.")
        reviewer_qs.append("Any blood in stool/vomit, severe abdominal pain, or dehydration signs?")
        if pathway_id in ("nausea_vomiting", "vomiting_child"):
            rx_review_only.append("If persistent nausea/vomiting without red flags: reviewer may consider antiemetic per guideline (reviewer selects).")

    elif pathway_id in ("rash_skin_infection", "contact_dermatitis", "hives_urticaria", "insect_bite_sting"):
        otc.append("cool compresses and avoid scratching")
        if pathway_id in ("hives_urticaria", "insect_bite_sting", "allergic_rhinitis"):
            otc.append("non-drowsy antihistamine may help itching/allergy symptoms if safe (no dosing provided)")
            cautions.append("If swelling of lips/tongue/face or trouble breathing: emergency care.")
        if pathway_id == "contact_dermatitis":
            otc.append("topical soothing measures (e.g., bland emollient) if skin is intact")
        if pathway_id == "rash_skin_infection":
            rx_review_only.append("If cellulitis suspected: reviewer to consider antibiotic per guideline.")
        reviewer_qs.append("Any rapidly spreading redness, fever, or severe pain suggesting infection?")

    elif pathway_id in ("ear_pain", "ear_pain_child"):
        add_basic_analgesics()
        reviewer_qs.append("Any mastoid signs (swelling behind ear), severe illness, or drainage?")
        rx_review_only.append("If AOM criteria met: consider antibiotic strategy per guideline (reviewer selects).")

    elif pathway_id in ("red_eye", "allergic_conjunctivitis"):
        otc.append("lubricating artificial tears may help irritation if safe (no dosing provided)")
        reviewer_qs.append("Any eye pain, light sensitivity, or vision change? If yes: urgent ophthalmic eval.")
        if pathway_id == "allergic_conjunctivitis":
            otc.append("cold compress may help itching")

    elif pathway_id in ("minor_burn", "minor_wound_care"):
        add_basic_analgesics()
        otc.append("gentle cleaning and protective covering (non-adherent dressing) if safe")
        reviewer_qs.append("Tetanus status? Consider booster if indicated (reviewer).")
        rx_review_only.append("If signs of infection: reviewer may consider antibiotic per guideline.")

    elif pathway_id in ("sprain_strain", "low_back_pain", "neck_pain_nontraumatic", "shoulder_pain_overuse"):
        add_basic_analgesics()
        otc.append("rest/ice/compression/elevation (RICE) if appropriate")
        cautions.append("Avoid NSAIDs if contraindicated; reviewer to confirm.")
        reviewer_qs.append("Any neurovascular compromise or severe functional limitation?")

    elif pathway_id in ("heartburn_gerd",):
        otc.append("avoid trigger foods; avoid lying down right after meals")
        otc.append("OTC antacid or acid-reducer may help if safe (no dosing provided)")
        reviewer_qs.append("Any exertional chest pressure, SOB, radiation symptoms? If yes: ED now.")
        rx_review_only.append("If persistent GERD symptoms: reviewer may consider PPI/H2 blocker plan per guideline.")

    elif pathway_id in ("med_refill",):
        # refill pathway may be different
        reviewer_qs.append("Confirm med list, allergies, and refill appropriateness per protocol.")

    else:
        # default: minimal safe OTC statement
        add_basic_analgesics()

    # De-duplicate while keeping order
    def dedupe(seq: List[str]) -> List[str]:
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return out

    return {
        "otc_options": dedupe(otc),
        "rx_review_only": dedupe(rx_review_only),
        "cautions": dedupe(cautions),
        "reviewer_questions": dedupe(reviewer_qs),
    }
