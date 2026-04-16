from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.rag import ingest_urls as iu


def ingest_pack_file(
    pack_path: str,
    *,
    time_budget_sec: float = 12.0,
    per_url_timeout_sec: int = 25,
    max_urls: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Ingest a URL pack into data/sources/library using existing helpers in app.rag.ingest_urls.

    Returns a list of ingested doc dicts: {title, url, text, source}
    """

    t0 = time.time()
    p = Path(pack_path)
    if not p.exists():
        return []

    try:
        pack_id, query, sources = iu.load_pack(p)
    except Exception:
        return []

    if not isinstance(sources, list):
        return []

    out: List[Dict[str, Any]] = []
    n_done = 0

    for src in sources:
        if max_urls is not None and n_done >= max_urls:
            break
        if (time.time() - t0) > float(time_budget_sec):
            break

        try:
            url = getattr(src, "url", None) or ""
            url = str(url).strip()
            if not url:
                continue

            # Fetch
            content, ctype = iu.fetch_url(url, timeout=int(per_url_timeout_sec))
            if not content:
                continue

            # Extract
            text, used_type = iu.extract_text(content, ctype or "", url)
            text = (text or "").strip()
            if not text:
                continue

            # Derive stable ID + output directory
            sid = iu.stable_id_for_url(url)
            out_dir = Path("data/sources/library") / sid
            out_dir.mkdir(parents=True, exist_ok=True)

            # Meta (title may be unknown here; upstream source.json can be improved later)
            meta = {
                "id": sid,
                "url": url,
                "title": "",  # keep blank if unknown (your existing library already has many like this)
                "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pack_id": pack_id,
                "query": query,
                "content_type": used_type or ctype or "",
                "source": "auto_acquire",
            }

            iu.write_source(out_dir, meta, text)

            out.append(
                {
                    "title": meta.get("title") or url,
                    "url": url,
                    "text": text[:4000],
                    "source": "auto_acquire",
                }
            )
            n_done += 1

        except Exception:
            continue

    # dedupe by URL preserve order
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for d in out:
        u = (d.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        deduped.append(d)

    return deduped
