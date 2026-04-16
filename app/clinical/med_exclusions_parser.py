from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class MedExclusionFlags:
    renal: bool
    liver: bool
    qt_risk: bool
    anticoagulant: bool
    gi_bleed_ulcer: bool
    pregnancy_possible: bool
    allergy_keywords: List[str]


_RENAL_PAT = re.compile(r"\b(ckd|kidney|renal|dialysis|end[-\s]?stage)\b", re.I)
_LIVER_PAT = re.compile(r"\b(liver|cirrhosis|hepatitis|hepatic|lft)\b", re.I)
_QT_PAT = re.compile(r"\b(qt|torsades|arrhythmia|afib|long\s?qt)\b", re.I)
_QT_MEDS_PAT = re.compile(r"\b(amiodarone|sotalol|dofetilide|flecainide|quinidine)\b", re.I)

_ANTICOAG_PAT = re.compile(
    r"\b(warfarin|coumadin|apixaban|eliquis|rivaroxaban|xarelto|dabigatran|pradaxa|heparin|enoxaparin|lovenox|blood thinner|anticoag)\b",
    re.I,
)
_GI_BLEED_PAT = re.compile(r"\b(gi bleed|gastrointestinal bleed|ulcer|peptic ulcer|melena|hematemesis)\b", re.I)


def parse_med_exclusions(
    med_exclusions_text: str,
    pregnancy_possible: str,
    allergies_text: str,
) -> MedExclusionFlags:
    t = (med_exclusions_text or "").strip()
    a = (allergies_text or "").strip()

    renal = bool(_RENAL_PAT.search(t))
    liver = bool(_LIVER_PAT.search(t))
    qt_risk = bool(_QT_PAT.search(t) or _QT_MEDS_PAT.search(t))
    anticoagulant = bool(_ANTICOAG_PAT.search(t))
    gi_bleed_ulcer = bool(_GI_BLEED_PAT.search(t))

    preg = (pregnancy_possible or "unknown").lower().strip() in {"yes", "unknown"}

    # Keyword-only allergy “flags” (conservative). This is not an ontology.
    allergy_keywords: List[str] = []
    lowa = a.lower()

    for kw in [
        "penicillin",
        "amoxicillin",
        "augmentin",
        "cephalosporin",
        "cef",
        "sulfa",
        "bactrim",
        "trimethoprim",
        "macrolide",
        "azithromycin",
        "clarithromycin",
        "erythromycin",
        "doxycycline",
        "tetracycline",
        "nsaid",
        "ibuprofen",
        "naproxen",
        "aspirin",
    ]:
        if kw in lowa:
            allergy_keywords.append(kw)

    return MedExclusionFlags(
        renal=renal,
        liver=liver,
        qt_risk=qt_risk,
        anticoagulant=anticoagulant,
        gi_bleed_ulcer=gi_bleed_ulcer,
        pregnancy_possible=preg,
        allergy_keywords=allergy_keywords,
    )
