from __future__ import annotations

from typing import Any, Dict, List
from app.core.models import EvidenceRef


def _clean_snippet(text: str, max_len: int = 360) -> str:
    t = (text or "").strip().replace("\r", " ").replace("\n", " ")
    while "  " in t:
        t = t.replace("  ", " ")
    if len(t) > max_len:
        t = t[:max_len].rstrip() + "…"
    return t


def as_evidence_refs(chunks: List[Dict[str, Any]]) -> List[EvidenceRef]:
    """
    Convert retrieve() dict chunks into EvidenceRef.

    Your EvidenceRef schema requires:
      - source (string)
      - snippet (string)
      - title (optional)

    IMPORTANT DESIGN CHOICE:
      We store the *URL* in EvidenceRef.source whenever available so the physician can click it.
      (EvidenceRef has no separate url field in your repo.)
    """
    out: List[EvidenceRef] = []
    seen = set()

    for c in (chunks or []):
        if not isinstance(c, dict):
            continue

        title = str(c.get("title") or "").strip()
        url = str(c.get("url") or "").strip()
        source_path = str(c.get("source") or "").strip()

        # Use URL as the canonical source when present
        source = url or source_path or title

        text = str(c.get("text") or "").strip()
        snippet = _clean_snippet(text) if text else _clean_snippet(title)

        if not source or not snippet:
            continue

        key = (source, title)
        if key in seen:
            continue
        seen.add(key)

        try:
            out.append(EvidenceRef(title=title, source=source, snippet=snippet))
        except Exception:
            continue

    return out
