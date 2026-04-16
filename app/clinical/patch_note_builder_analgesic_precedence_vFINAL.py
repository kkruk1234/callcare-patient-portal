#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

NEW_FUNC = '''
def _cc_adjust_plan_analgesics_for_organ_disease(soap_text: str, state) -> str:
    """
    Analgesic precedence:
      - Renal disease → acetaminophen first-line
      - Else liver disease → NSAID first-line
      - Else → no change
    """
    if not soap_text:
        return soap_text

    text = soap_text.lower()

    def yes(v):
        return str(v).strip().lower() in ("yes", "true", "1")

    renal = False
    liver = False

    try:
        renal = yes(getattr(state, "renal_disease", None)) or yes(state.answers.get("renal_disease"))
    except Exception:
        pass

    try:
        liver = yes(getattr(state, "liver_disease", None)) or yes(state.answers.get("liver_disease"))
    except Exception:
        pass

    lines = soap_text.splitlines()

    # find Plan section
    p0 = None
    for i,l in enumerate(lines):
        if l.strip().lower().startswith(("p:", "plan")):
            p0 = i
            break
    if p0 is None:
        return soap_text

    p1 = len(lines)
    for i in range(p0+1, len(lines)):
        if lines[i].strip().lower().startswith(("s:", "subjective", "a:", "assessment")):
            p1 = i
            break

    plan = lines[p0:p1]

    def is_apap(s): return "acetaminophen" in s.lower()
    def is_nsaid(s): return any(k in s.lower() for k in ("ibuprofen","naproxen","diclofenac","nsaid"))

    # find first-line content line
    fl_i = None
    for i,l in enumerate(plan):
        if "first-line" in l.lower():
            for j in range(i+1, min(i+3,len(plan))):
                if plan[j].strip():
                    fl_i = j
                    break
            break

    if fl_i is None:
        return soap_text

    fl = plan[fl_i]

    # precedence
    if renal and is_nsaid(fl):
        for l in plan:
            if is_apap(l):
                plan[fl_i] = l.strip()
                break

    elif liver and not renal and is_apap(fl):
        for l in plan:
            if is_nsaid(l):
                plan[fl_i] = l.strip()
                break

    lines[p0:p1] = plan
    return "\\n".join(lines)
'''

def main():
    txt = P.read_text()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_ANALGESIC_FINAL_{ts}")
    bak.write_text(txt)

    # remove old function if present
    for name in [
        "_cc_adjust_plan_analgesics_for_renal",
        "_cc_adjust_plan_analgesics_for_organ_disease"
    ]:
        k = f"def {name}("
        if k in txt:
            s = txt.find(k)
            e = txt.find("\\ndef ", s+1)
            if e == -1:
                e = len(txt)
            txt = txt[:s] + txt[e+1:]

    # insert before build_note
    anchor = "def build_note"
    i = txt.find(anchor)
    txt = txt[:i] + NEW_FUNC + "\\n\\n" + txt[i:]

    # ensure call exists
    call = "soap_text = _cc_adjust_plan_analgesics_for_organ_disease(soap_text, state)"
    if call not in txt:
        txt = txt.replace(
            "soap_text = _cc_dedupe_exact_sentence_once(soap_text, preg_sentence)",
            "soap_text = _cc_dedupe_exact_sentence_once(soap_text, preg_sentence)\\n    " + call,
            1
        )

    P.write_text(txt)
    print("OK: analgesic precedence applied")
    print("Backup:", bak)

if __name__ == "__main__":
    main()
