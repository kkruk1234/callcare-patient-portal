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
    bak = P.with_suffix(P.suffix + f".bak_STOPPRUNE_{ts}")
    bak.write_text(txt, encoding="utf-8")

    changed = 0

    # 1) Relax min_title_hits=2 -> 1 (both topup calls)
    before = "min_title_hits=2"
    after = "min_title_hits=1"
    if before in txt:
        txt2 = txt.replace(before, after)
        changed += (txt2 != txt)
        txt = txt2

    # 2) Disable prune line entirely (comment it out)
    prune_line = "evidence = _cc_prune_evidence_to_titlematch(state, evidence, min_title_hits=2)"
    prune_line_v1 = "evidence = _cc_prune_evidence_to_titlematch(state, evidence, min_title_hits=1)"
    if prune_line in txt:
        txt = txt.replace(prune_line, "# DISABLED (was collapsing Evidence Used to 1): " + prune_line)
        changed += 1
    if prune_line_v1 in txt:
        txt = txt.replace(prune_line_v1, "# DISABLED (was collapsing Evidence Used to 1): " + prune_line_v1)
        changed += 1

    P.write_text(txt, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    print(f"Edits applied: {changed}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
