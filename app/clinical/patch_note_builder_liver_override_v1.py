#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_LIVEROVR_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Add a liver flag helper if missing
    if "def _cc_has_liver_disease" not in txt:
        insert_at = txt.find("def _cc_has_renal_disease")
        if insert_at == -1:
            print("ERROR: could not find renal helper anchor")
            return 2

        helper = '''
def _cc_has_liver_disease(state) -> bool:
    """
    Robustly infer liver disease flag from CallState.
    """
    def _is_yes(v) -> bool:
        if v is True:
            return True
        s = str(v or "").strip().lower()
        return s in ("yes", "y", "true", "1")

    for attr in ("liver_disease", "hepatic_disease", "has_liver_disease"):
        try:
            if _is_yes(getattr(state, attr, None)):
                return True
        except Exception:
            pass

    try:
        ans = getattr(state, "answers", None)
        if isinstance(ans, dict):
            for k in ("liver_disease", "hepatic_disease", "cirrhosis", "hepatitis"):
                if k in ans and _is_yes(ans.get(k)):
                    return True
    except Exception:
        pass

    return False
'''
        txt = txt[:insert_at] + helper + "\n" + txt[insert_at:]

    # Patch the function _cc_adjust_plan_analgesics_for_renal to implement precedence.
    # We'll insert a small block near the top where renal_flag is determined.
    marker = "if not renal_flag:\n        return soap_text"
    if marker not in txt:
        print("ERROR: could not find renal early-return marker in adjuster")
        return 2

    if "__CC_LIVER_OVERRIDE" in txt:
        print("OK: liver override already present; no changes.")
        return 0

    block = '''
    # __CC_LIVER_OVERRIDE: analgesic precedence
    # If NO renal disease but liver disease is present, prefer NSAID-first-line and demote acetaminophen.
    liver_flag = False
    try:
        liver_flag = bool(_cc_has_liver_disease(state))
    except Exception:
        liver_flag = False
    if (not liver_flag) and any(k in low_all for k in ("liver disease", "hepatic", "cirrhosis", "hepatitis")):
        liver_flag = True

    if (not renal_flag) and liver_flag:
        # We will swap in NSAID first-line if acetaminophen is currently first-line.
        # Continue processing below, but with a liver-first preference flag.
        pass
'''

    txt = txt.replace(marker, block + "\n" + marker, 1)

    # Now add the liver-first swap logic right after we compute fl_content (we can reuse existing detectors).
    anchor2 = "if not is_nsaid(fl_content):\n        return soap_text"
    if anchor2 not in txt:
        print("ERROR: could not find first-line non-NSAID early return anchor")
        return 2

    swap_logic = '''
    # __CC_LIVER_OVERRIDE_SWAP
    # If liver disease and no renal disease, and acetaminophen is first-line, prefer NSAID first-line.
    try:
        if (not renal_flag) and liver_flag:
            if is_apap(fl_content):
                # Find an NSAID line anywhere in plan (prefer Alternatives)
                nsaid_idx2 = None
                for ii, ln2 in enumerate(plan):
                    if is_nsaid(ln2):
                        nsaid_idx2 = ii
                        break
                if nsaid_idx2 is not None:
                    nsaid_line2 = plan[nsaid_idx2].strip()
                    if nsaid_line2.lower().startswith("-"):
                        nsaid_line2 = nsaid_line2.lstrip("-").strip()

                    # Demote APAP line into Alternatives with liver caution
                    apap_line2 = fl_content.strip()
                    if apap_line2.lower().startswith("-"):
                        apap_line2 = apap_line2.lstrip("-").strip()

                    # Replace first-line content with NSAID content
                    plan[fl_content_idx] = " " + nsaid_line2 if plan[fl_content_idx].startswith(" ") else nsaid_line2

                    # Remove NSAID from elsewhere to avoid duplicates
                    if nsaid_idx2 != fl_content_idx:
                        plan.pop(nsaid_idx2)

                    # Ensure Alternatives header exists
                    alt_hdr2 = None
                    for jj, ln3 in enumerate(plan):
                        if is_alt_header(ln3):
                            alt_hdr2 = jj
                            break
                    if alt_hdr2 is None:
                        insert_at2 = fl_content_idx + 1
                        plan.insert(insert_at2, "")
                        plan.insert(insert_at2 + 1, "- Alternatives:")
                        alt_hdr2 = insert_at2 + 1

                    plan.insert(alt_hdr2 + 1, f" - {apap_line2} (use caution in liver disease; consider lower max daily dose)")

                    new_txt2 = "\\n".join(lines[:plan_i] + plan + lines[end_i:])
                    new_txt2 = new_txt2.replace("  ", " ").replace("..", ".").replace(" .", ".")
                    return new_txt2
    except Exception:
        pass
'''

    txt = txt.replace(anchor2, swap_logic + "\n" + anchor2, 1)

    P.write_text(txt, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
