#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/note_builder.py")

HELPER = r'''
def _cc_dedupe_exact_sentence_once(text: str, sentence: str) -> str:
    """
    Keep at most ONE exact occurrence of `sentence` in `text`.
    Deterministic string ops only.
    """
    t = text or ""
    s = (sentence or "").strip()
    if not t or not s:
        return t

    # Normalize sentence to ensure trailing period
    if not s.endswith("."):
        s = s + "."

    # If <=1 occurrence, nothing to do
    if t.count(s) <= 1:
        return t

    # Remove all, then re-insert once in Subjective via existing insertion helper if available.
    t = t.replace(s, "")
    # Clean up leftover double spaces and punctuation artifacts
    while "  " in t:
        t = t.replace("  ", " ")
    t = t.replace("..", ".").replace(" .", ".").replace(" \n", "\n")

    # Try to re-insert as last line of Subjective using existing helper
    try:
        t = _insert_preg_sentence_as_last_subjective_sentence(t, s)
    except Exception:
        # fallback: append at end
        t = (t.rstrip() + "\n" + s + "\n")
    return t
'''

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_PREGDEDUP_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Inject helper before build_note if not present
    if "def _cc_dedupe_exact_sentence_once(" not in txt:
        anchor = "def build_note(state: CallState) -> NoteDraft:"
        idx = txt.find(anchor)
        if idx == -1:
            print("ERROR: could not find build_note anchor")
            return 2
        txt = txt[:idx] + HELPER + "\n\n" + txt[idx:]

    # Ensure we call it AFTER all pregnancy manipulation.
    # We'll insert right after the existing strip-redundant call if present,
    # otherwise right after the pregnancy insertion line.
    call = "soap_text = _cc_dedupe_exact_sentence_once(soap_text, preg_sentence)"

    if call not in txt:
        anchor1 = "soap_text = _strip_redundant_no_preg_pos_sentence(soap_text, preg_sentence)"
        anchor2 = "soap_text = _insert_preg_sentence_as_last_subjective_sentence(soap_text, preg_sentence)"

        if anchor1 in txt:
            txt = txt.replace(anchor1, anchor1 + "\n\n    " + call, 1)
        elif anchor2 in txt:
            txt = txt.replace(anchor2, anchor2 + "\n\n    " + call, 1)
        else:
            print("ERROR: could not find pregnancy postprocess anchor to insert dedupe call")
            return 2

    P.write_text(txt, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
