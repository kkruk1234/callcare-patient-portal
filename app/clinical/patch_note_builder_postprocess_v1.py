#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

HELPER = r'''
def _cc_postprocess_soap_final(soap_text: str, state, preg_sentence: str) -> str:
    """
    FINAL deterministic cleanup (no clinical logic changes):
      - Ensure pregnancy sentence appears ONLY once and ONLY as last sentence of Subjective.
      - If Subjective has 'no renal disease or pregnancy' phrasing, remove 'or pregnancy' (preg handled separately).
      - Fix double-dash bullets ("- - X" -> "- X").
      - Normalize Plan: if no 'First-line medication:' header but a med bullet exists, insert header.
      - Remove contradictory 'Dosing details were not found...' when dosing is already present in the same line.
      - Remove renal suffix when renal disease is NO; if present, capitalize + period.
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
    try:
        renal = renal or _is_yes(getattr(state, "renal_disease", None)) or _is_yes(getattr(state, "kidney_disease", None))
    except Exception:
        pass
    renal = renal or _is_yes(_ans("renal_disease")) or _is_yes(_ans("kidney_disease")) or _is_yes(_ans("reduced_kidney_function")) or _is_yes(_ans("ckd"))

    # Basic line cleanup
    lines = soap_text.splitlines()
    cleaned = []
    for ln in lines:
        # "- - foo" -> "- foo"
        if ln.lstrip().startswith("- - "):
            # preserve indentation
            prefix_len = len(ln) - len(ln.lstrip())
            cleaned.append((" " * prefix_len) + "- " + ln.lstrip()[4:])
        else:
            cleaned.append(ln)
    lines = cleaned
    txt = "\n".join(lines)

    # Pregnancy sentence normalization
    ps = (preg_sentence or "").strip()
    if ps:
        if not ps.endswith("."):
            ps = ps + "."

        # Remove awkward combined phrase in Subjective like "no renal disease or pregnancy"
        if "no renal disease or pregnancy" in txt.lower():
            # do a case-insensitive-ish replacement by handling common exact casing patterns
            txt = txt.replace("no renal disease or pregnancy", "no renal disease")
            txt = txt.replace("No renal disease or pregnancy", "No renal disease")
            txt = txt.replace("no renal disease or Pregnancy", "no renal disease")
            txt = txt.replace("No renal disease or Pregnancy", "No renal disease")

        # Remove ALL occurrences of pregnancy sentence anywhere
        if ps in txt:
            txt = txt.replace(ps, "").replace("  ", " ").replace("..", ".").replace(" .", ".")

        # Reinsert as last sentence of Subjective (use your existing helper if available)
        try:
            txt = _insert_preg_sentence_as_last_subjective_sentence(txt, ps)
        except Exception:
            # fallback append
            txt = txt.rstrip() + "\n" + ps + "\n"

        # Hard dedupe in case it appears twice
        if txt.count(ps) > 1:
            first = txt.find(ps)
            txt = txt[:first + len(ps)] + txt[first + len(ps):].replace(ps, "")

    # Plan normalization
    lines = txt.splitlines()

    # find Plan section start
    plan_i = None
    for i, ln in enumerate(lines):
        l = ln.strip().lower()
        if l in ("p:", "plan:", "plan") or l.startswith("p:") or l.startswith("plan:"):
            plan_i = i
            break
    if plan_i is not None:
        # find end of Plan
        headings = ("subjective:", "s:", "objective:", "o:", "assessment:", "a:", "mdm:", "medical decision", "differential:")
        end_i = len(lines)
        for j in range(plan_i + 1, len(lines)):
            l = lines[j].strip().lower()
            if any(l.startswith(h) for h in headings):
                end_i = j
                break

        plan = lines[plan_i:end_i]
        plan_low = "\n".join(plan).lower()

        # Insert "First-line medication:" header if missing but a medication bullet exists
        if "first-line medication" not in plan_low:
            # find first med-ish bullet line
            meds = ("acetaminophen", "paracetamol", "tylenol", "ibuprofen", "naproxen", "diclofenac", "celecoxib")
            first_med_idx = None
            for k in range(1, len(plan)):  # skip "P:" line
                s = plan[k].strip().lower()
                if s.startswith("-") and any(m in s for m in meds):
                    first_med_idx = k
                    break
            if first_med_idx is not None:
                plan.insert(first_med_idx, "- First-line medication:")

        # Remove contradictory dosing disclaimer if line already has explicit dosing (mg + frequency)
        out = []
        for ln in plan:
            low = ln.lower()
            if "dosing details were not found in retrieved evidence" in low:
                has_mg = (" mg" in low) or ("mg " in low) or ("mg/" in low)
                has_freq = ("every " in low) or ("q" in low) or ("twice daily" in low) or ("daily" in low)
                if has_mg and has_freq:
                    cut = low.find("dosing details were not found in retrieved evidence")
                    ln = ln[:cut].rstrip().rstrip(".") + "."
            out.append(ln)
        plan = out

        # Renal suffix handling
        out = []
        for ln in plan:
            low = ln.lower()
            k = low.find("(avoid in renal disease")
            if k != -1:
                if not renal:
                    # drop entirely if renal is no
                    ln = ln[:k].rstrip()
                else:
                    # normalize capitalization + period
                    # replace from '(' onward with standardized suffix
                    ln = ln[:k].rstrip() + " (Avoid in renal disease; especially severe CKD/dialysis.)"
            out.append(ln)
        plan = out

        lines = lines[:plan_i] + plan + lines[end_i:]

    return "\n".join(lines)
'''

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2
    txt = P.read_text(encoding="utf-8", errors="ignore")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_POSTPROC_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Insert helper once before build_note
    if "def _cc_postprocess_soap_final(" not in txt:
        anchor = "def build_note(state: CallState) -> NoteDraft:"
        i = txt.find(anchor)
        if i == -1:
            print("ERROR: could not find build_note anchor")
            return 2
        txt = txt[:i] + HELPER + "\n\n" + txt[i:]

    call = "soap_text = _cc_postprocess_soap_final(soap_text, state, preg_sentence)"
    if call not in txt:
        # Place it late, after pregnancy insertion if present; otherwise after preg_sentence is computed.
        a1 = "soap_text = _insert_preg_sentence_as_last_subjective_sentence(soap_text, preg_sentence)"
        a2 = "preg_sentence = _preg_sentence_from_norm(preg_norm)"
        if a1 in txt:
            txt = txt.replace(a1, a1 + "\n\n    " + call, 1)
        elif a2 in txt:
            txt = txt.replace(a2, a2 + "\n\n    " + call, 1)
        else:
            print("ERROR: could not find safe anchor to add postprocess call")
            return 2

    P.write_text(txt, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
