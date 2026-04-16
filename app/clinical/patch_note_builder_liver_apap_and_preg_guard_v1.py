#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

HELPER = r'''
def _cc_postprocess_plan_otc_liver_and_disclaimers(soap_text: str, state, preg_sentence: str = "") -> str:
    """
    Deterministic cleanup:
      - If liver disease YES and renal disease NO: clamp acetaminophen to 500 mg q6h PRN, max 2 g/day.
      - Remove 'Dosing details were not found...' from acetaminophen/OTC analgesic bullets.
      - Remove '(avoid in renal disease; ...)' suffix when renal disease is NO.
      - Guarantee preg_sentence appears once in Subjective if provided.
    """
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

    renal = False
    liver = False

    try:
        renal = renal or _is_yes(getattr(state, "renal_disease", None)) or _is_yes(getattr(state, "kidney_disease", None))
    except Exception:
        pass
    renal = renal or _is_yes(_ans("renal_disease")) or _is_yes(_ans("kidney_disease")) or _is_yes(_ans("reduced_kidney_function")) or _is_yes(_ans("ckd"))

    try:
        liver = liver or _is_yes(getattr(state, "liver_disease", None)) or _is_yes(getattr(state, "hepatic_disease", None))
    except Exception:
        pass
    liver = liver or _is_yes(_ans("liver_disease")) or _is_yes(_ans("hepatic_disease")) or _is_yes(_ans("cirrhosis")) or _is_yes(_ans("hepatitis"))

    low_all = soap_text.lower()
    if not renal and any(k in low_all for k in ("renal disease", "renal impairment", "chronic kidney", "ckd", "dialysis", "egfr")):
        renal = True
    if not liver and any(k in low_all for k in ("liver disease", "hepatic", "cirrhosis", "hepatitis")):
        liver = True

    lines = soap_text.splitlines()

    # --- Remove renal suffix when renal is NO ---
    if not renal:
        cleaned = []
        for ln in lines:
            low = ln.lower()
            k = low.find(" (avoid in renal disease")
            if k != -1:
                ln = ln[:k].rstrip()
            cleaned.append(ln)
        lines = cleaned

    # --- Clamp acetaminophen dosing for liver disease (when renal is NO) ---
    # Target: any plan bullet mentioning acetaminophen/paracetamol.
    if liver and (not renal):
        out = []
        for ln in lines:
            low = ln.lower()
            if "acetaminophen" in low or "paracetamol" in low or "tylenol" in low:
                # Remove the generic dosing-disclaimer sentence if it's appended on the same line
                if "dosing details were not found in retrieved evidence" in low:
                    # hard cut at that phrase
                    cut = low.find("dosing details were not found in retrieved evidence")
                    ln = ln[:cut].rstrip().rstrip(".") + "."
                # Replace wide range with liver-safe outpatient wording if it contains 500–1000mg q6h style
                # We do simple substring replacements; if not present, we still append max-dose guidance.
                replaced = ln
                # Normalize hyphen variants
                for wide in ("500–1000 mg", "500-1000 mg", "500–1000mg", "500-1000mg", "500 to 1000 mg", "500 to 1000mg"):
                    if wide in replaced:
                        replaced = replaced.replace(wide, "500 mg")
                # If it says "every 6 hours" but no max, add max 2 g/day
                low2 = replaced.lower()
                if ("every 6" in low2 or "q6" in low2) and ("max" not in low2) and ("2 g" not in low2) and ("2000" not in low2):
                    # add concise max
                    replaced = replaced.rstrip().rstrip(".") + "; max 2 g/day in liver disease."
                # If still no frequency but acetaminophen exists, add max guidance
                if ("acetaminophen" in low2 or "paracetamol" in low2) and ("max 2 g/day" not in replaced.lower()):
                    if "liver disease" in low_all and "max" not in replaced.lower():
                        replaced = replaced.rstrip().rstrip(".") + " Max 2 g/day in liver disease."
                out.append(replaced)
            else:
                out.append(ln)
        lines = out

    # --- Guarantee pregnancy sentence appears once (if provided) ---
    ps = (preg_sentence or "").strip()
    if ps:
        if not ps.endswith("."):
            ps = ps + "."
        joined = "\n".join(lines)
        if joined.count(ps) > 1:
            # remove all then reinsert once
            joined = joined.replace(ps, "")
            while "  " in joined:
                joined = joined.replace("  ", " ")
            joined = joined.replace("..", ".").replace(" .", ".")
            lines = joined.splitlines()

        joined = "\n".join(lines)
        if ps not in joined:
            try:
                joined = _insert_preg_sentence_as_last_subjective_sentence(joined, ps)
            except Exception:
                joined = joined.rstrip() + "\n" + ps + "\n"
            # dedupe again just in case
            if joined.count(ps) > 1:
                joined = joined.replace(ps, "", joined.count(ps)-1)
            lines = joined.splitlines()

    return "\n".join(lines)
'''

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8", errors="ignore")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_LIVERAPAP_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Insert helper once before build_note
    if "def _cc_postprocess_plan_otc_liver_and_disclaimers(" not in txt:
        anchor = "def build_note(state: CallState) -> NoteDraft:"
        i = txt.find(anchor)
        if i == -1:
            print("ERROR: could not find build_note anchor")
            return 2
        txt = txt[:i] + HELPER + "\n\n" + txt[i:]

    # Add call near the end of build_note AFTER pregnancy insertion/dedupe (so it can guarantee presence)
    call = "soap_text = _cc_postprocess_plan_otc_liver_and_disclaimers(soap_text, state, preg_sentence)"
    if call not in txt:
        # Place it after your pregnancy dedupe call if present, otherwise after insertion
        a1 = "soap_text = _cc_dedupe_exact_sentence_once(soap_text, preg_sentence)"
        a2 = "soap_text = _insert_preg_sentence_as_last_subjective_sentence(soap_text, preg_sentence)"
        if a1 in txt:
            txt = txt.replace(a1, a1 + "\n\n    " + call, 1)
        elif a2 in txt:
            txt = txt.replace(a2, a2 + "\n\n    " + call, 1)
        else:
            print("ERROR: could not find pregnancy anchor for placement")
            return 2

    P.write_text(txt, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
