from __future__ import annotations

from typing import Any, Dict, Optional


def ensure_evidence_dict(h: Dict[str, Any], *, snippet_max_chars: int = 360) -> Dict[str, Any]:
    """
    Defensive normalizer so NoteDraft never receives evidence entries missing required keys.
    Especially important for auto-acquired / freshly ingested docs.
    """
    if h is None:
        h = {}

    out: Dict[str, Any] = dict(h)

    # Common fields across your pipeline; tolerate differences.
    title = (out.get("title") or "").strip()
    url = (out.get("url") or "").strip()
    source = (out.get("source") or "").strip()

    # Try several likely fields that might contain text.
    text = (
        out.get("snippet")
        or out.get("abstract")
        or out.get("extract")
        or out.get("content")
        or out.get("text")
        or ""
    )
    if text is None:
        text = ""
    text = str(text).strip()

    # Guarantee required-ish fields.
    if "snippet" not in out or not str(out.get("snippet") or "").strip():
        if text:
            out["snippet"] = text[:snippet_max_chars]
        else:
            # Last-ditch fallback: make a human-readable snippet so validation never fails.
            bits = []
            if title:
                bits.append(title)
            if source:
                bits.append(source)
            if url:
                bits.append(url)
            out["snippet"] = " | ".join(bits)[:snippet_max_chars] if bits else "No snippet available."

    # Keep these consistent if present.
    if title and "title" not in out:
        out["title"] = title
    if url and "url" not in out:
        out["url"] = url
    if source and "source" not in out:
        out["source"] = source

    return out
