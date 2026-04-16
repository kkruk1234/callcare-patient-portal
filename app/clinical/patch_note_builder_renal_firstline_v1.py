#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

HELPER = r'''
def _cc_has_renal_disease(state) -> bool:
    """
    Robustly infer renal disease flag from CallState.
    Accepts bool-like fields and common answer keys.
    """
    def _is_yes(v) -> bool:
        if v is True:
            return True
        s = str(v or "").strip().lower()
        return s in ("yes", "y", "true", "1")

    # direct attributes (varies across versions)
    for attr in ("renal_disease", "kidney_disease", "has_renal_disease", "reduced_kidney_function"):
        try:
            if _is_yes(getattr(state, attr, None)):
                return True
        except Exception:
            pass

    # answers dict-like
    try:
        ans = getattr(state, "answers", None)
        if isinstance(ans, dict):
            for k in ("renal_disease", "kidney_disease", "reduced_kidney_function", "ckd", "has_ckd"):
                if k in ans and _is_yes(ans.get(k)):
                    return True
    except Exception:
        pass

    return False


def _cc_adjust_plan_analgesics_for_renal(soap_text: str, state) -> str:
    """
    If renal disease is present, avoid NSAIDs as FIRST-LINE analgesic.
    Promote acetaminophen to first-line when available.
    Deterministic string/line operations only (no regex).
    """
    if not soap_text or not _cc_has_renal_disease(state):
        return soap_text

    lines = soap_text.splitlines()

    # locate Plan section start
    plan_i = None
    for i, ln in enumerate(lines):
        l = ln.strip().lower()
        if l in ("p:", "plan:", "plan") or l.startswith("p:") or l.startswith("plan:"):
            plan_i = i
            break
    if plan_i is None:
        return soap_text

    # locate Plan section end
    headings = ("objective:", "assessment:", "a:", "subjective:", "s:", "mdm:", "medical decision")
    end_i = len(lines)
    for j in range(plan_i + 1, len(lines)):
        l = lines[j].strip().lower()
        if any(l.startswith(h) for h in headings):
            end_i = j
            break

    plan = lines[plan_i:end_i]
    lower_plan = [p.lower() for p in plan]

    def is_firstline_idx(idx: int) -> bool:
        return "first-line medication" in lower_plan[idx]

    def is_nsaid_line(s: str) -> bool:
        s = s.lower()
        return ("ibuprofen" in s) or ("naproxen" in s) or ("diclofenac" in s) or ("celecoxib" in s) or ("nsaid" in s)

    def is_apap_line(s: str) -> bool:
        s = s.lower()
        return ("acetaminophen" in s) or ("paracetamol" in s) or ("tylenol" in s)

    first_idx = None
    for i in range(len(plan)):
        if is_firstline_idx(i):
            first_idx = i
            break
    if first_idx is None:
        return soap_text

    first_line = plan[first_idx]
    if not is_nsaid_line(first_line):
        # first-line is not an NSAID; nothing to change
        return soap_text

    # find an acetaminophen line anywhere in Plan block
    apap_idx = None
    for i in range(len(plan)):
        if is_apap_line(plan[i]):
            apap_idx = i
            break

    # Ensure we have an Alternatives section marker (best-effort)
    alt_i = None
    for i in range(len(plan)):
        if "alternatives" in lower_plan[i]:
            alt_i = i
            break

    # Build a renal-pref first-line acetaminophen line.
    # If we found an existing APAP line, use it; otherwise insert a conservative one.
    if apap_idx is not None:
        apap_line = plan[apap_idx]
    else:
        apap_line = " - First-line medication: Acetaminophen 500-1000 mg orally every 6 hours as needed for pain; max 3 grams/day (adjust for liver disease)."

    # Demote NSAID first-line into Alternatives with renal caution (keep their content)
    nsaid_demoted = first_line
    if "first-line medication" in nsaid_demoted.lower():
        nsaid_demoted = nsaid_demoted.replace("First-line medication:", "Alternative (avoid in renal disease):")
        nsaid_demoted = nsaid_demoted.replace("First-line Medication:", "Alternative (avoid in renal disease):")

    # Replace first-line with APAP
    plan[first_idx] = apap_line if "first-line medication" in apap_line.lower() else (" - First-line medication: " + apap_line.lstrip("- ").strip())

    # Remove the old APAP line if it was elsewhere (to avoid duplicates)
    if apap_idx is not None and apap_idx != first_idx:
        # If apap was in alternatives, remove it from there since it's now first-line
        plan.pop(apap_idx)
        if apap_idx < first_idx:
            first_idx -= 1

    # Make sure Alternatives section exists; if not, add it
    if alt_i is None:
        # insert after first-line line
        insert_at = min(first_idx + 1, len(plan))
        plan.insert(insert_at, " - Alternatives:")
        alt_i = insert_at

    # Insert demoted NSAID under Alternatives (right after Alternatives header)
    insert_at = min(alt_i + 1, len(plan))
    plan.insert(insert_at, nsaid_demoted)

    # Also make sure medication safety line clearly states NSAID avoidance in renal disease
    safety_i = None
    for i in range(len(plan)):
        if "medication safety" in lower_plan[i]:
            safety_i = i
            break
    if safety_i is not None:
        # strengthen wording (append if not already present)
        s = plan[safety_i]
        if "avoid nsaids" not in s.lower():
            plan[safety_i] = s.rstrip() + " Avoid NSAIDs in renal disease (especially severe CKD/dialysis)."
    else:
        # add a safety line if missing
        plan.append(" - Medication safety considerations: Patient has renal disease; avoid NSAIDs (especially severe CKD/dialysis).")

    # Reassemble
    new_lines = lines[:plan_i] + plan + lines[end_i:]
    return "\n".join(new_lines)
'''

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_RENALFIRST_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Inject helper before build_note so it's defined
    if "def _cc_adjust_plan_analgesics_for_renal(" not in txt:
        anchor = "def build_note(state: CallState) -> NoteDraft:"
        idx = txt.find(anchor)
        if idx == -1:
            print("ERROR: could not find build_note anchor")
            return 2
        txt = txt[:idx] + HELPER + "\n\n" + txt[idx:]

    # Add call AFTER pregnancy insertion (so pregnancy stays correct)
    call_line = "soap_text = _cc_adjust_plan_analgesics_for_renal(soap_text, state)"
    if call_line not in txt:
        # place it right after the pregnancy insertion line (present in your snippet)
        anchor2 = "soap_text = _insert_preg_sentence_as_last_subjective_sentence(soap_text, preg_sentence)"
        if anchor2 not in txt:
            print("ERROR: could not find pregnancy insertion anchor to place renal adjustment")
            return 2
        txt = txt.replace(anchor2, anchor2 + "\n\n    " + call_line, 1)

    P.write_text(txt, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
