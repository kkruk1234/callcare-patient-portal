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
    bak = P.with_suffix(P.suffix + f".bak_RENALFIRSTV3_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Find the exact insert line used by v2 and replace it with a cleaned version.
    old = "plan.insert(insert_at, f\" - {nsaid_line} (avoid in renal disease; especially severe CKD/dialysis)\")"

    if old not in txt:
        print("ERROR: could not find v2 insert line to patch.")
        return 2

    new = (
        "    # Clean demoted line: remove any leftover 'First-line medication:' label\n"
        "    nl = nsaid_line\n"
        "    for pref in (\"First-line medication:\", \"First-Line medication:\", \"First-line Medication:\", \"FIRST-LINE MEDICATION:\"):\n"
        "        if pref in nl:\n"
        "            nl = nl.replace(pref, \"\").strip()\n"
        "    plan.insert(insert_at, f\" - {nl} (avoid in renal disease; especially severe CKD/dialysis)\")"
    )

    txt = txt.replace(old, new, 1)

    P.write_text(txt, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
