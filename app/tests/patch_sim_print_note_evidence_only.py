#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/tests/simulate_text_call.py")

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8", errors="ignore")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_PRINTNOTEEVID_{ts}")
    bak.write_text(txt, encoding="utf-8")

    lines = txt.splitlines(True)

    marker = "note = build_note(state)"
    out = []
    inserted = False
    in_run_after_note = False
    patched_calls = 0

    for line in lines:
        out.append(line)

        if (not inserted) and (marker in line):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}# CallCare: ensure Evidence Used is printed from the built note.evidence\n")
            out.append(f"{indent}__cc_note_evidence = list(getattr(note, 'evidence', None) or [])\n")
            inserted = True
            in_run_after_note = True
            continue

        # After we've inserted the note evidence var, patch ONLY subsequent _print_evidence calls
        if in_run_after_note and ("_print_evidence(" in line) and (not line.lstrip().startswith("def ")):
            indent = line[:len(line) - len(line.lstrip())]
            # always pass state if available in scope (it is, in run)
            out[-1] = f"{indent}_print_evidence(__cc_note_evidence, state)\n"
            patched_calls += 1

    if not inserted:
        print("ERROR: could not find marker:", marker)
        return 2

    P.write_text("".join(out), encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    print(f"_print_evidence calls patched after build_note: {patched_calls}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
