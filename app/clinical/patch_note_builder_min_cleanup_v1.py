#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

HELPER = '''
def _cc_min_cleanup_preg_and_liver_apap(soap_text: str, state, preg_sentence: str) -> str:
    """
    MINIMAL cleanup only (do not change clinical structure):
      1) Fix 'Patient is not pregnant.,' punctuation.
      2) Ensure exactly one pregnancy sentence.
      3) If liver disease YES and renal disease NO:
           - clamp acetaminophen dose range to 500 mg every 6 hours (keep rest of wording)
           - remove any '(avoid in renal disease ...)' suffix (since renal is NO)
      4) If Plan starts with '- Alternatives:' and lacks 'First-line medication:', insert header above Alternatives.
    """
    if not soap_text:
        return soap_text

    def _is_yes(v) -> bool:
        if v is True:
            return True
        s = str(v or "").strip().lower()
        return s in ("yes","y","true","1")

    # flags
    renal = False
    liver = False
    try:
        renal = renal or _is_yes(getattr(state, "renal_disease", None)) or _is_yes(getattr(state, "kidney_disease", None))
    except Exception:
        pass
    try:
        ans = getattr(state, "answers", None)
        if isinstance(ans, dict):
            renal = renal or _is_yes(ans.get("renal_disease")) or _is_yes(ans.get("kidney_disease")) or _is_yes(ans.get("reduced_kidney_function")) or _is_yes(ans.get("ckd"))
            liver = liver or _is_yes(ans.get("liver_disease")) or _is_yes(ans.get("hepatic_disease")) or _is_yes(ans.get("cirrhosis")) or _is_yes(ans.get("hepatitis"))
    except Exception:
        pass
    try:
        liver = liver or _is_yes(getattr(state, "liver_disease", None)) or _is_yes(getattr(state, "hepatic_disease", None))
    except Exception:
        pass

    # --- 1) pregnancy punctuation fix + 2) dedupe ---
    ps = (preg_sentence or "").strip()
    if ps and not ps.endswith("."):
        ps = ps + "."

    # Fix the exact bad punctuation artifact we saw: "Patient is not pregnant.,"
    soap_text = soap_text.replace("Patient is not pregnant.,", "Patient is not pregnant.")

    # If we have the canonical sentence, dedupe it to one occurrence (keep first)
    if ps and soap_text.count(ps) > 1:
        first = soap_text.find(ps)
        soap_text = soap_text[:first + len(ps)] + soap_text[first + len(ps):].replace(ps, "")

    # --- split lines for Plan edits ---
    lines = soap_text.splitlines()

    # find Plan block
    plan_i = None
    for i, ln in enumerate(lines):
        l = ln.strip().lower()
        if l in ("p:", "plan:", "plan") or l.startswith("p:") or l.startswith("plan:"):
            plan_i = i
            break
    if plan_i is not None:
        end_i = len(lines)
        headings = ("subjective:", "s:", "objective:", "o:", "assessment:", "a:", "differential:")
        for j in range(plan_i + 1, len(lines)):
            lj = lines[j].strip().lower()
            if any(lj.startswith(h) for h in headings):
                end_i = j
                break

        plan = lines[plan_i:end_i]
        plan_low = "\\n".join(plan).lower()

        # --- 4) restore First-line header if plan begins with Alternatives and header missing ---
        if "first-line medication" not in plan_low:
            # if we have Alternatives header, insert First-line above it (keeps structure)
            alt_idx = None
            for k, pl in enumerate(plan):
                if "alternatives" in (pl or "").lower():
                    alt_idx = k
                    break
            if alt_idx is not None:
                # Only insert if Alternatives is very early (meaning first thing listed)
                # and there is at least one med line under it.
                if alt_idx <= 2:
                    plan.insert(alt_idx, "- First-line medication:")

        # --- 3) liver disease (no renal): clamp APAP line + remove renal suffix ---
        if liver and (not renal):
            new_plan = []
            for pl in plan:
                low = pl.lower()

                # Remove renal suffix if it appears anywhere (renal is NO)
                k = low.find(" (avoid in renal disease")
                if k != -1:
                    pl = pl[:k].rstrip()

                # Clamp acetaminophen dose range only if present
                low2 = pl.lower()
                if "acetaminophen" in low2 or "paracetamol" in low2 or "tylenol" in low2:
                    # Replace 500–1000 / 500-1000 variants with 500 mg, keep rest
                    for v in ("500–1000 mg","500-1000 mg","500 to 1000 mg","500–1000mg","500-1000mg","500 to 1000mg"):
                        if v in pl:
                            pl = pl.replace(v, "500 mg")
                    # Ensure it says max 2 g/day if it already mentions q6h/every 6 hours
                    low3 = pl.lower()
                    if ("every 6" in low3 or "q6" in low3) and ("max 2 g/day" not in low3) and ("2 g/day" not in low3):
                        pl = pl.rstrip().rstrip(".") + "; max 2 g/day in liver disease."
                new_plan.append(pl)

            plan = new_plan

        lines = lines[:plan_i] + plan + lines[end_i:]

    return "\\n".join(lines)
'''

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8", errors="ignore")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_MINCLEAN_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Insert helper before build_note
    if "def _cc_min_cleanup_preg_and_liver_apap(" not in txt:
        anchor = "def build_note(state: CallState) -> NoteDraft:"
        i = txt.find(anchor)
        if i == -1:
            print("ERROR: build_note anchor not found")
            return 2
        txt = txt[:i] + HELPER + "\\n\\n" + txt[i:]

    call = "soap_text = _cc_min_cleanup_preg_and_liver_apap(soap_text, state, preg_sentence)"
    if call not in txt:
        # Put this AFTER pregnancy insertion (so it cleans duplicates/punct after insertion)
        a = "soap_text = _insert_preg_sentence_as_last_subjective_sentence(soap_text, preg_sentence)"
        if a not in txt:
            print("ERROR: pregnancy insertion anchor not found")
            return 2
        txt = txt.replace(a, a + "\\n\\n    " + call, 1)

    P.write_text(txt, encoding="utf-8")
    print("OK: patched", P)
    print("Backup:", bak)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
