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
    bak = P.with_suffix(P.suffix + f".bak_PRINTBUILTEVID_{ts}")
    bak.write_text(txt, encoding="utf-8")

    lines = txt.splitlines(True)

    out = []
    built_var_inserted = False
    print_call_patched = 0

    for line in lines:
        # Remove prior debug block if present (the one printing DEBUG_EVID_NOTE_BUILDER)
        if "DEBUG_EVID_NOTE_BUILDER" in line:
            # skip this line; also skip the surrounding injected block lines conservatively
            continue

        out.append(line)

        # Insert built_evidence capture immediately after note = build_note(state)
        if (not built_var_inserted) and ("note = build_note(state)" in line):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}built_evidence = list(getattr(note, 'evidence', None) or [])\n")
            built_var_inserted = True

    # Now patch the call site(s) of _print_evidence to use built_evidence if available.
    # We do this as a second pass so we can match lines cleanly.
    out2 = []
    for line in out:
        if "_print_evidence(" in line:
            # Keep indentation and preserve the original second arg "state" if present,
            # but replace the first argument with built_evidence.
            indent = line[:len(line) - len(line.lstrip())]
            # naive but safe: any call becomes _print_evidence(built_evidence, state)
            if "state" in line:
                out2.append(f"{indent}_print_evidence(built_evidence, state)\n")
            else:
                out2.append(f"{indent}_print_evidence(built_evidence)\n")
            print_call_patched += 1
        else:
            out2.append(line)

    P.write_text("".join(out2), encoding="utf-8")

    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    print(f"Inserted built_evidence: {built_var_inserted}")
    print(f"_print_evidence calls patched: {print_call_patched}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
