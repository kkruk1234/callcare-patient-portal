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

    start_key = "def _rebuild_evidence_from_differential("
    # This is the next function in your file per your earlier snippet
    end_key = "def _gate_rx_candidates_by_note_text("

    s = txt.find(start_key)
    e = txt.find(end_key)

    if s == -1 or e == -1 or e <= s:
        print("ERROR: Could not locate rebuild function block to replace.")
        print(f"Found start={s} end={e}")
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_DISABLE_REBUILD_{ts}")
    bak.write_text(txt, encoding="utf-8")

    replacement = (
        "def _rebuild_evidence_from_differential(note, state, max_total=6, min_total=3):\n"
        "    \"\"\"\n"
        "    DISABLED.\n"
        "    This function was overwriting/shrinking Evidence Used and reintroducing rule-out noise\n"
        "    (e.g., pericarditis) despite having condition-specific outpatient sources available.\n"
        "    Evidence selection is handled upstream in note_builder.py.\n"
        "    \"\"\"\n"
        "    return note\n\n"
    )

    new_txt = txt[:s] + replacement + txt[e:]
    P.write_text(new_txt, encoding="utf-8")

    print(f"OK: Disabled _rebuild_evidence_from_differential in {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
