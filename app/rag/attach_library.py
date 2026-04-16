import json
from pathlib import Path
from typing import List, Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
LIB_DIR = BASE_DIR / "data" / "sources" / "library"

def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _read_text(p: Path, limit: int = 3500) -> str:
    try:
        t = p.read_text(encoding="utf-8", errors="ignore").strip()
        if len(t) > limit:
            return t[:limit] + "…"
        return t
    except Exception:
        return ""

def find_library_doc_by_url(url: str) -> Optional[Dict]:
    if not url:
        return None
    url = url.strip()
    if not LIB_DIR.exists():
        return None

    # Scan library entries for matching source.json url
    # (Library size can be large; but this runs only when auto-acquire triggers.)
    for d in LIB_DIR.iterdir():
        if not d.is_dir():
            continue
        sj = d / "source.json"
        if not sj.exists():
            continue
        meta = _read_json(sj)
        if not isinstance(meta, dict):
            continue
        u = str(meta.get("url") or "").strip()
        if u == url:
            title = str(meta.get("title") or meta.get("name") or "Guideline excerpt").strip()
            content_path_txt = d / "content.txt"
            # some versions store main text differently; try a couple common names
            if not content_path_txt.exists():
                for alt in ("text.txt", "content.md"):
                    ap = d / alt
                    if ap.exists():
                        content_path_txt = ap
                        break
            text = _read_text(content_path_txt, limit=3500)
            return {
                "title": title,
                "url": url,
                "source": url,
                "text": text,
            }
    return None

def evidence_dicts_for_urls(urls: List[str], max_refs: int = 2) -> List[Dict]:
    out: List[Dict] = []
    seen = set()
    for u in (urls or []):
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        doc = find_library_doc_by_url(u)
        if doc and doc.get("text"):
            out.append(doc)
        if len(out) >= max_refs:
            break
    return out
