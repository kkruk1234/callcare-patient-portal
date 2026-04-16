#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

NEW_FUNC = """
def _cc_adjust_plan_analgesics_for_renal(soap_text: str, state) -> str:
    \"""
    Analgesic precedence (deterministic, general):
      1) Renal disease -> acetaminophen first-line (avoid NSAIDs)
      2) Else liver disease -> NSAID first-line (limit acetaminophen)
      3) Else -> no change

    Handles both Plan formats:
      A) Has 'First-line medication:' header (drug may be on next line)
      B) No header; first medication bullet under P: is treated as first-line

    Also:
      - strips leftover 'First-line medication:' labels from demoted lines
      - removes '(avoid in renal disease...)' when renal is false
    \"""
    if not soap_text:
        return soap_text

    def _is_yes(v) -> bool:
        if v is True:
            return True
        s = str(v or "").strip().lower()
        return s in ("yes", "y", "true", "1")

    def _ans(key: str):
        try:
            a = getattr(state, "answers", None)
            if isinstance(a, dict):
                return a.get(key)
        except Exception:
            return None
        return None

    # --- flags ---
    renal = False
    liver = False

    # Prefer existing helper if present
    try:
        renal = bool(_cc_has_renal_disease(state))
    except Exception:
        renal = False

    renal = renal or _is_yes(getattr(state, "renal_disease", None)) or _is_yes(getattr(state, "kidney_disease", None))
    renal = renal or _is_yes(_ans("renal_disease")) or _is_yes(_ans("kidney_disease")) or _is_yes(_ans("reduced_kidney_function")) or _is_yes(_ans("ckd"))

    liver = liver or _is_yes(getattr(state, "liver_disease", None)) or _is_yes(getattr(state, "hepatic_disease", None))
    liver = liver or _is_yes(_ans("liver_disease")) or _is_yes(_ans("hepatic_disease")) or _is_yes(_ans("cirrhosis")) or _is_yes(_ans("hepatitis"))

    low_all = soap_text.lower()
    if not renal and any(k in low_all for k in ("renal disease", "renal impairment", "chronic kidney", "ckd", "dialysis", "egfr")):
        renal = True
    if not liver and any(k in low_all for k in ("liver disease", "hepatic", "cirrhosis", "hepatitis")):
        liver = True

    if not renal and not liver:
        return soap_text

    def is_apap(s: str) -> bool:
        s = (s or "").lower()
        return ("acetaminophen" in s) or ("paracetamol" in s) or ("tylenol" in s)

    def is_nsaid(s: str) -> bool:
        s = (s or "").lower()
        return any(d in s for d in ("ibuprofen", "naproxen", "diclofenac", "celecoxib", "indomethacin")) or ("nsaid" in s)

    def strip_firstline_label(s: str) -> str:
        t = (s or "").strip()
        for pref in ("First-line medication:", "First-Line medication:", "First-line Medication:", "FIRST-LINE MEDICATION:"):
            if pref in t:
                t = t.replace(pref, "").strip()
        return t

    def strip_renal_suffix_if_not_renal(s: str) -> str:
        if renal:
            return s
        t = (s or "")
        low = t.lower()
        k = low.find(" (avoid in renal disease")
        if k != -1:
            return t[:k].rstrip()
        return t

    def looks_like_bullet(s: str) -> bool:
        st = (s or "").lstrip()
        return st.startswith("-") or st.startswith("•")

    # --- find Plan block ---
    lines = soap_text.splitlines()

    plan_i = None
    for i, ln in enumerate(lines):
        l = ln.strip().lower()
        if l in ("p:", "plan:", "plan") or l.startswith("p:") or l.startswith("plan:"):
            plan_i = i
            break
    if plan_i is None:
        return soap_text

    headings = ("subjective:", "s:", "objective:", "o:", "assessment:", "a:", "mdm:", "medical decision")
    end_i = len(lines)
    for j in range(plan_i + 1, len(lines)):
        l = lines[j].strip().lower()
        if any(l.startswith(h) for h in headings):
            end_i = j
            break

    plan = lines[plan_i:end_i]

    # find Alternatives header if present
    alt_hdr = None
    for i, ln in enumerate(plan):
        if "alternatives" in (ln or "").lower():
            alt_hdr = i
            break

    def ensure_alt(after_idx: int) -> int:
        nonlocal alt_hdr, plan
        if alt_hdr is not None:
            return alt_hdr
        insert_at = min(after_idx + 1, len(plan))
        plan.insert(insert_at, " - Alternatives:")
        alt_hdr = insert_at
        return alt_hdr

    # Determine first-line content line
    fl_hdr = None
    for i, ln in enumerate(plan):
        if "first-line medication" in (ln or "").lower():
            fl_hdr = i
            break

    fl_idx = None
    fl_line = None

    if fl_hdr is not None:
        # header exists; drug may be on same line or next non-empty line
        if is_apap(plan[fl_hdr]) or is_nsaid(plan[fl_hdr]):
            fl_idx, fl_line = fl_hdr, plan[fl_hdr]
        else:
            k = fl_hdr + 1
            while k < len(plan) and plan[k].strip() == "":
                k += 1
            if k < len(plan):
                fl_idx, fl_line = k, plan[k]
    else:
        # no header; treat first bullet after P: as first-line
        k = 1
        while k < len(plan) and (plan[k].strip() == "" or not looks_like_bullet(plan[k]) or "alternatives" in plan[k].lower()):
            k += 1
        if k < len(plan):
            fl_idx, fl_line = k, plan[k]

    if fl_idx is None or fl_line is None:
        return soap_text

    # Find candidate NSAID and APAP lines in plan (excluding headers)
    apap_idx = None
    nsaid_idx = None
    for i, ln in enumerate(plan):
        low = (ln or "").lower()
        if "alternatives" in low or "first-line medication" in low:
            continue
        if apap_idx is None and is_apap(ln):
            apap_idx = i
        if nsaid_idx is None and is_nsaid(ln):
            nsaid_idx = i
        if apap_idx is not None and nsaid_idx is not None:
            break

    # --- precedence ---
    # Renal wins over liver
    if renal and is_nsaid(fl_line):
        # promote APAP
        if apap_idx is not None:
            apap_line = strip_firstline_label(plan[apap_idx]).lstrip("-").strip()
        else:
            apap_line = "Acetaminophen 500–1000 mg orally every 6 hours as needed for pain; max 3 grams/day (adjust for liver disease)."

        demoted = strip_firstline_label(fl_line).lstrip("-").strip()
        demoted = strip_renal_suffix_if_not_renal(demoted)
        demoted = f"{demoted} (avoid in renal disease; especially severe CKD/dialysis)"

        plan[fl_idx] = ("- " + apap_line) if looks_like_bullet(plan[fl_idx]) else apap_line

        # remove promoted apap elsewhere
        if apap_idx is not None and apap_idx != fl_idx:
            plan.pop(apap_idx)
            if apap_idx < fl_idx:
                fl_idx -= 1
            if alt_hdr is not None and apap_idx < alt_hdr:
                alt_hdr -= 1

        ah = ensure_alt(fl_idx)
        plan.insert(ah + 1, f" - {demoted}")
        return "\\n".join(lines[:plan_i] + plan + lines[end_i:])

    if (liver and not renal) and is_apap(fl_line):
        # promote NSAID
        if nsaid_idx is None:
            return soap_text

        nsaid_line = strip_firstline_label(plan[nsaid_idx]).lstrip("-").strip()
        nsaid_line = strip_renal_suffix_if_not_renal(nsaid_line)

        apap_demoted = strip_firstline_label(fl_line).lstrip("-").strip()
        apap_demoted = strip_renal_suffix_if_not_renal(apap_demoted)
        apap_demoted = f"{apap_demoted} (use caution in liver disease; consider lower max daily dose)"

        plan[fl_idx] = ("- " + nsaid_line) if looks_like_bullet(plan[fl_idx]) else nsaid_line

        if nsaid_idx != fl_idx:
            plan.pop(nsaid_idx)
            if nsaid_idx < fl_idx:
                fl_idx -= 1
            if alt_hdr is not None and nsaid_idx < alt_hdr:
                alt_hdr -= 1

        ah = ensure_alt(fl_idx)
        plan.insert(ah + 1, f" - {apap_demoted}")
        return "\\n".join(lines[:plan_i] + plan + lines[end_i:])

    return soap_text
"""

def main() -> int:
    txt = P.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_SAFEANALG_{ts}")
    bak.write_text(txt, encoding="utf-8")

    key = "def _cc_adjust_plan_analgesics_for_renal("
    s = txt.find(key)
    if s == -1:
        print("ERROR: function not found")
        return 2

    n = txt.find("\ndef ", s + 1)
    if n == -1:
        n = len(txt)

    # IMPORTANT: only real newlines are written (no literal \\n sequences)
    new_txt = txt[:s] + NEW_FUNC + "\n\n" + txt[n+1:]
    P.write_text(new_txt, encoding="utf-8")

    print("OK: replaced analgesic function safely")
    print("Backup:", bak)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
