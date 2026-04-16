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
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = P.with_suffix(P.suffix + f".bak_EVIDDEBUG_{ts}")
    bak.write_text(txt, encoding="utf-8")

    marker = "note = build_note(state)"
    if marker not in txt:
        print("ERROR: could not find build_note call in simulate_text_call.py")
        return 2

    if "DEBUG_EVID_NOTE_BUILDER" in txt:
        print("OK: debug already present; no changes.")
        return 0

    injection = (
        marker + "\n"
        "            # DEBUG_EVID_NOTE_BUILDER\n"
        "            try:\n"
        "                ev = getattr(note, 'evidence', None)\n"
        "                n = len(ev) if isinstance(ev, list) else 0\n"
        "                print(f\"\\nDEBUG_EVID_NOTE_BUILDER: count={n}\")\n"
        "                if isinstance(ev, list):\n"
        "                    for i, e in enumerate(ev[:10], start=1):\n"
        "                        t = getattr(e, 'title', None) or (e.get('title') if isinstance(e, dict) else '')\n"
        "                        u = getattr(e, 'url', None) or getattr(e, 'source', None) or (e.get('url') or e.get('source') if isinstance(e, dict) else '')\n"
        "                        print(f\"  {i}. {t} | {u}\")\n"
        "            except Exception as _e:\n"
        "                print(f\"DEBUG_EVID_NOTE_BUILDER: failed: {_e}\")\n"
    )

    txt2 = txt.replace(marker, injection, 1)
    P.write_text(txt2, encoding="utf-8")
    print(f"OK: patched {P}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
