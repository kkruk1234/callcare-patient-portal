#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/clinical/finalize.py")

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_PRESERVEEVID_{ts}")
    bak.write_text(txt, encoding="utf-8")

    if "__CC_ORIG_EVIDENCE" in txt:
        print("OK: preserve-evidence patch already present; no changes.")
        return 0

    # Insert capture right after finalize(...) signature line
    lines = txt.splitlines(True)
    out = []
    inserted_top = False
    inserted_bottom = False

    for i, line in enumerate(lines):
        out.append(line)

        if (not inserted_top) and line.lstrip().startswith("def finalize("):
            # next line after def is usually indentation block; we insert immediately after def line
            indent = " " * 4
            out.append(f"{indent}# __CC_ORIG_EVIDENCE: preserve evidence produced by build_note() (avoid downstream overwrite)\n")
            out.append(f"{indent}__CC_ORIG_EVIDENCE = list(getattr(note, 'evidence', None) or [])\n")
            inserted_top = True

    # Now insert restore just before "return packet" (last occurrence)
    # We'll do a second pass for safety.
    out2 = []
    for line in out:
        if (not inserted_bottom) and line.lstrip().startswith("return packet"):
            indent = line[:len(line) - len(line.lstrip())]
            out2.append(f"{indent}# __CC_ORIG_EVIDENCE: restore original evidence so Evidence Used reflects build_note()\n")
            out2.append(f"{indent}if __CC_ORIG_EVIDENCE:\n")
            out2.append(f"{indent}    try:\n")
            out2.append(f"{indent}        note.evidence = __CC_ORIG_EVIDENCE\n")
            out2.append(f"{indent}    except Exception:\n")
            out2.append(f"{indent}        pass\n")
            inserted_bottom = True
        out2.append(line)

    if not inserted_top:
        print("ERROR: could not find def finalize(")
        return 2
    if not inserted_bottom:
        print("ERROR: could not find 'return packet' to insert restore")
        return 2

    P.write_text("".join(out2), encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
