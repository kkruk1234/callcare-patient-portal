#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

def replace_def_block(txt: str, name: str, new_block: str) -> str:
    key = f"def {name}("
    s = txt.find(key)
    if s == -1:
        raise RuntimeError(f"Could not find {name}")
    n = txt.find("\ndef ", s + 1)
    if n == -1:
        n = len(txt)
    return txt[:s] + new_block.rstrip() + "\n\n" + txt[n+1:]

NEW_DEF = r'''
def _cc_adjust_plan_analgesics_for_renal(soap_text: str, state) -> str:
    """
    Deterministic Plan post-processor:
      - If renal disease is present (state OR note text), avoid NSAIDs as FIRST-LINE analgesic.
      - Works even when the NSAID is on the line AFTER "First-line medication:".
      - Promotes an existing acetaminophen line from Alternatives if present.
    """
    if not soap_text:
        return soap_text

    txt = soap_text

    # Trigger if state says renal disease OR the text itself suggests renal disease/impairment
    renal_flag = False
    try:
        renal_flag = bool(_cc_has_renal_disease(state))
    except Exception:
        renal_flag = False

    low_all = txt.lower()
    if (not renal_flag) and any(k in low_all for k in ("renal disease", "renal impairment", "chronic kidney", "ckd", "dialysis", "eGFR".lower())):
        renal_flag = True

    if not renal_flag:
        return soap_text

    lines = txt.splitlines()

    # Find Plan section
    plan_i = None
    for i, ln in enumerate(lines):
        l = ln.strip().lower()
        if l in ("p:", "plan:", "plan") or l.startswith("p:") or l.startswith("plan:"):
            plan_i = i
            break
    if plan_i is None:
        return soap_text

    # End of Plan section
    headings = ("subjective:", "s:", "objective:", "o:", "assessment:", "a:", "mdm:", "medical decision")
    end_i = len(lines)
    for j in range(plan_i + 1, len(lines)):
        l = lines[j].strip().lower()
        if any(l.startswith(h) for h in headings):
            end_i = j
            break

    plan = lines[plan_i:end_i]

    def is_header_firstline(s: str) -> bool:
        return "first-line medication" in (s or "").strip().lower()

    def is_alt_header(s: str) -> bool:
        return "alternatives" in (s or "").strip().lower()

    def is_nsaid(s: str) -> bool:
        s = (s or "").lower()
        return any(d in s for d in ("ibuprofen", "naproxen", "diclofenac", "celecoxib", "indomethacin")) or (" nsaid" in s) or ("nsaid" in s)

    def is_apap(s: str) -> bool:
        s = (s or "").lower()
        return any(d in s for d in ("acetaminophen", "paracetamol", "tylenol"))

    # Locate "First-line medication" header
    fl_hdr = None
    for i, ln in enumerate(plan):
        if is_header_firstline(ln):
            fl_hdr = i
            break
    if fl_hdr is None:
        return soap_text

    # The actual first-line content may be on the same line or the next non-empty line(s)
    fl_content = None
    fl_content_idx = None

    # Check same line first
    if is_nsaid(plan[fl_hdr]) or is_apap(plan[fl_hdr]):
        fl_content_idx = fl_hdr
        fl_content = plan[fl_hdr]
    else:
        # next non-empty line
        k = fl_hdr + 1
        while k < len(plan) and plan[k].strip() == "":
            k += 1
        if k < len(plan):
            fl_content_idx = k
            fl_content = plan[k]

    if fl_content is None or fl_content_idx is None:
        return soap_text

    # If first-line content is not NSAID, nothing to do
    if not is_nsaid(fl_content):
        return soap_text

    # Find an acetaminophen line in Plan (prefer from Alternatives)
    apap_idx = None
    for i, ln in enumerate(plan):
        if is_apap(ln):
            apap_idx = i
            break

    # Build acetaminophen first-line content
    if apap_idx is not None:
        apap_line = plan[apap_idx].strip()
    else:
        apap_line = "Acetaminophen 500–1000 mg orally every 6 hours as needed for pain; max 3 grams/day (adjust for liver disease)."

    # Normalize acetaminophen line formatting to match where it will live (content line, not header)
    if apap_line.lower().startswith("-"):
        apap_line = apap_line.lstrip("-").strip()

    # Prepare demoted NSAID line to go under Alternatives
    nsaid_line = fl_content.strip()
    if nsaid_line.lower().startswith("-"):
        nsaid_line = nsaid_line.lstrip("-").strip()

    # Replace first-line content line with acetaminophen content
    plan[fl_content_idx] = " " + apap_line if plan[fl_content_idx].startswith(" ") else apap_line

    # Remove acetaminophen from elsewhere if we promoted it (avoid duplicates)
    if apap_idx is not None and apap_idx != fl_content_idx:
        # if we removed a line above fl_content_idx, adjust indices not needed since we won't reuse
        plan.pop(apap_idx)
        # if apap was before fl_content_idx, fl_content_idx shifts up by 1, but we don't need it now

    # Ensure Alternatives header exists
    alt_hdr = None
    for i, ln in enumerate(plan):
        if is_alt_header(ln):
            alt_hdr = i
            break
    if alt_hdr is None:
        # insert Alternatives block after first-line block
        insert_at = fl_content_idx + 1
        plan.insert(insert_at, "")
        plan.insert(insert_at + 1, "- Alternatives:")
        alt_hdr = insert_at + 1

    # Insert NSAID under Alternatives (immediately after header)
    insert_at = alt_hdr + 1
    plan.insert(insert_at, f" - {nsaid_line} (avoid in renal disease; especially severe CKD/dialysis)")

    # Reassemble
    new_txt = "\n".join(lines[:plan_i] + plan + lines[end_i:])

    # Minor cleanup of doubled spaces
    new_txt = new_txt.replace("  ", " ")
    return new_txt
'''

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2
    txt = P.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_RENALFIRSTV2_{ts}")
    bak.write_text(txt, encoding="utf-8")

    try:
        txt2 = replace_def_block(txt, "_cc_adjust_plan_analgesics_for_renal", NEW_DEF)
    except Exception as e:
        print("ERROR:", e)
        print("This means the v1 function name wasn't found in note_builder.py.")
        return 2

    P.write_text(txt2, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
