#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

P = Path("app/tests/simulate_text_call.py")

def main() -> int:
    if not P.exists():
        print(f"ERROR: missing {P}")
        return 2

    txt = P.read_text(encoding="utf-8")
    if "DEBUG_EVID_NOTE_BUILDER:" in txt:
        print("OK: debug already present; no changes.")
        return 0

    lines = txt.splitlines(True)  # keep newlines
    marker = "note = build_note(state)"

    out = []
    changed = False

    for line in lines:
        out.append(line)
        if (marker in line) and (not changed):
            indent = line[:len(line) - len(line.lstrip())]

            block = [
                f"{indent}# DEBUG_EVID_NOTE_BUILDER\n",
                f"{indent}try:\n",
                f"{indent}    ev = getattr(note, 'evidence', None)\n",
                f"{indent}    n = len(ev) if isinstance(ev, list) else 0\n",
                f"{indent}    print(f\"\\nDEBUG_EVID_NOTE_BUILDER: count={{n}}\")\n",
                f"{indent}    if isinstance(ev, list):\n",
                f"{indent}        for i, e in enumerate(ev[:10], start=1):\n",
                f"{indent}            t = getattr(e, 'title', None) or (e.get('title') if isinstance(e, dict) else '')\n",
                f"{indent}            u = getattr(e, 'url', None) or getattr(e, 'source', None) or (e.get('url') or e.get('source') if isinstance(e, dict) else '')\n",
                f"{indent}            print(f\"  {{i}}. {{t}} | {{u}}\")\n",
                f"{indent}except Exception as _e:\n",
                f"{indent}    print(f\"DEBUG_EVID_NOTE_BUILDER: failed: {{_e}}\")\n",
            ]
            out.extend(block)
            changed = True

    if not changed:
        print("ERROR: could not find marker line:", marker)
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_EVIDDEBUGV3_{ts}")
    bak.write_text(txt, encoding="utf-8")

    P.write_text("".join(out), encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
