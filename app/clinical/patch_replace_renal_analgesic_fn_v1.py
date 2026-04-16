#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

NEW_FUNC = '''def _cc_adjust_plan_analgesics_for_renal(soap_text: str, state) -> str:
    """
    Analgesic precedence (deterministic):
      1) Renal disease -> acetaminophen first-line (avoid NSAIDs)
      2) Else liver disease -> NSAID first-line (use caution with acetaminophen)
      3) Else -> no change

    Also fixes formatting bug where a demoted NSAID line kept the 'First-line medication:' label.
    Works when drug is on the line AFTER 'First-line medication:'.
    """
    if not soap_text:
        return soap_text

    # --- robust flags (state OR note text) ---
    def _is_yes(v) -> bool:
        if v is True:
            return True
        s = str(v or "").strip().lower()
        return s in ("yes", "y", "true", "1")

    def _get_answer(key: str):
        try:
            ans = getattr(state, "answers", None)
            if isinstance(ans, dict):
                return ans.get(key)
        except Exception:
            return None
        return None

    renal_flag = False
    liver_flag = False

    # Prefer existing helpers if they exist in this file
    try:
        renal_flag = bool(_cc_has_renal_disease(state))
    except Exception:
        renal_flag = False

    # Direct attrs / answers fallbacks
    try:
        renal_flag = renal_flag or _is_yes(getattr(state, "renal_disease", None)) or _is_yes(getattr(state, "kidney_disease", None))
    except Exception:
        pass
    renal_flag = renal_flag or _is_yes(_get_answer("renal_disease")) or _is_yes(_get_answer("kidney_disease")) or _is_yes(_get_answer("reduced_kidney_function"))

    try:
        liver_flag = _is_yes(getattr(state, "liver_disease", None)) or _is_yes(getattr(state, "hepatic_disease", None))
    except Exception:
        pass
    liver_flag = liver_flag or _is_yes(_get_answer("liver_disease")) or _is_yes(_get_answer("hepatic_disease")) or _is_yes(_get_answer("cirrhosis")) or _is_yes(_get_answer("hepatitis"))

    low_all = soap_text.lower()
    if not renal_flag and any(k in low_all for k in ("renal disease", "renal impairment", "chronic kidney", "ckd", "dialysis", "egfr")):
        renal_flag = True
    if not liver_flag and any(k in low_all for k in ("liver disease", "hepatic", "cirrhosis", "hepatitis")):
        liver_flag = True

    # If no relevant organ flags, nothing to do
    if not renal_flag and not liver_flag:
        return soap_text

    # --- helpers ---
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

    # locate First-line header and its content line
    fl_hdr = None
    for i, ln in enumerate(plan):
        if "first-line medication" in (ln or "").lower():
            fl_hdr = i
            break
    if fl_hdr is None:
        return soap_text

    fl_content_idx = None
    fl_content = None

    # If header contains drug on same line (rare), use it; otherwise next non-empty line
    if is_apap(plan[fl_hdr]) or is_nsaid(plan[fl_hdr]):
        fl_content_idx = fl_hdr
        fl_content = plan[fl_hdr]
    else:
        k = fl_hdr + 1
        while k < len(plan) and plan[k].strip() == "":
            k += 1
        if k < len(plan):
            fl_content_idx = k
            fl_content = plan[k]

    if fl_content_idx is None or fl_content is None:
        return soap_text

    # find Alternatives header (optional)
    alt_hdr = None
    for i, ln in enumerate(plan):
        if "alternatives" in (ln or "").lower():
            alt_hdr = i
            break

    def ensure_alternatives_header(after_idx: int) -> int:
        nonlocal alt_hdr, plan
        if alt_hdr is not None:
            return alt_hdr
        insert_at = min(after_idx + 1, len(plan))
        plan.insert(insert_at, " - Alternatives:")
        alt_hdr = insert_at
        return alt_hdr

    # Collect candidate lines
    apap_idx = None
    nsaid_idx = None
    for i, ln in enumerate(plan):
        if apap_idx is None and is_apap(ln):
            apap_idx = i
        if nsaid_idx is None and is_nsaid(ln):
            nsaid_idx = i
        if apap_idx is not None and nsaid_idx is not None:
            break

    # --- precedence decision ---
    # Renal wins over liver if both present.
    want_apap_first = renal_flag
    want_nsaid_first = (liver_flag and not renal_flag)

    # Case 1: renal -> promote APAP if NSAID currently first-line
    if want_apap_first and is_nsaid(fl_content):
        # choose APAP line from plan if present; else conservative APAP line
        if apap_idx is not None:
            apap_line = plan[apap_idx].strip()
        else:
            apap_line = "Acetaminophen 500–1000 mg orally every 6 hours as needed for pain; max 3 grams/day (adjust for liver disease)."

        apap_line = strip_firstline_label(apap_line).lstrip("-").strip()

        # demote NSAID content line under Alternatives
        demoted = strip_firstline_label(fl_content).lstrip("-").strip()
        if demoted:
            demoted = f"{demoted} (avoid in renal disease; especially severe CKD/dialysis)"

        # replace first-line content
        plan[fl_content_idx] = (" " + apap_line) if plan[fl_content_idx].startswith(" ") else apap_line

        # remove promoted APAP from elsewhere if it wasn't already the first-line content
        if apap_idx is not None and apap_idx != fl_content_idx:
            plan.pop(apap_idx)
            # adjust indices if needed
            if apap_idx < fl_content_idx:
                fl_content_idx -= 1
                fl_hdr -= 1 if apap_idx < fl_hdr else 0
            if alt_hdr is not None and apap_idx < alt_hdr:
                alt_hdr -= 1

        ah = ensure_alternatives_header(fl_content_idx)
        plan.insert(ah + 1, f" - {demoted}")

        return "\\n".join(lines[:plan_i] + plan + lines[end_i:])

    # Case 2: liver (no renal) -> promote NSAID if APAP currently first-line
    if want_nsaid_first and is_apap(fl_content):
        # choose NSAID line from plan if present; else do nothing
        if nsaid_idx is None:
            return soap_text

        nsaid_line = strip_firstline_label(plan[nsaid_idx]).lstrip("-").strip()
        apap_demoted = strip_firstline_label(fl_content).lstrip("-").strip()
        if apap_demoted:
            apap_demoted = f"{apap_demoted} (use caution in liver disease; consider lower max daily dose)"

        # set first-line content to NSAID
        plan[fl_content_idx] = (" " + nsaid_line) if plan[fl_content_idx].startswith(" ") else nsaid_line

        # remove NSAID from elsewhere if it wasn't already the first-line content
        if nsaid_idx != fl_content_idx:
            plan.pop(nsaid_idx)
            if nsaid_idx < fl_content_idx:
                fl_content_idx -= 1
                fl_hdr -= 1 if nsaid_idx < fl_hdr else 0
            if alt_hdr is not None and nsaid_idx < alt_hdr:
                alt_hdr -= 1

        ah = ensure_alternatives_header(fl_content_idx)
        plan.insert(ah + 1, f" - {apap_demoted}")

        return "\\n".join(lines[:plan_i] + plan + lines[end_i:])

    return soap_text
'''

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_REPLFN_{ts}")
    bak.write_text(txt, encoding="utf-8")

    name = "def _cc_adjust_plan_analgesics_for_renal("
    s = txt.find(name)
    if s == -1:
        print("ERROR: could not find _cc_adjust_plan_analgesics_for_renal")
        return 2

    n = txt.find("\ndef ", s + 1)
    if n == -1:
        n = len(txt)

    # Replace the entire function block
    new_txt = txt[:s] + NEW_FUNC + "\n\n" + txt[n+1:]
    P.write_text(new_txt, encoding="utf-8")

    print(f"OK: replaced _cc_adjust_plan_analgesics_for_renal in {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
