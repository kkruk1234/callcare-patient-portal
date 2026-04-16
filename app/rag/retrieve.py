import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# If FAISS is disabled, retrieve() MUST return [] quickly and never import faiss/numpy.
CALLCARE_DISABLE_FAISS = os.environ.get("CALLCARE_DISABLE_FAISS", "").strip().lower() in ("1", "true", "yes")

if not CALLCARE_DISABLE_FAISS:
    import faiss
    import numpy as np
    from app.clinical.embeddings import get_embedder

# Resolve project root reliably (…/callcare/)
BASE_DIR = Path(__file__).resolve().parents[2]
INDEX_DIR = BASE_DIR / "data" / "index"
FAISS_PATH = INDEX_DIR / "faiss.index"
DOCS_PATH = INDEX_DIR / "docs.json"

_model = None
_index = None
_DOCS = None

def reset_index_cache():
    global _model, _index, _DOCS
    _model = None
    _index = None
    _DOCS = None

def _debug_enabled() -> bool:
    return os.environ.get("RAG_DEBUG", "").strip().lower() in ("1", "true", "yes")

_WORD_RE = re.compile(r"[a-z0-9]+")

def _tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    return _WORD_RE.findall(text)

def _relevance_score(query_tokens: List[str], doc: Dict, diagnosis_hint: Optional[str]) -> float:
    title = (doc.get("title") or "").lower()
    text = (doc.get("text") or "").lower()
    src = (doc.get("source") or "").lower()
    url = (doc.get("url") or "").lower()
    hay = " ".join([title, src, url, text[:2000]])
    if not query_tokens:
        overlap = 0.0
    else:
        hits = 0
        denom = 0
        for t in query_tokens:
            if len(t) <= 2:
                continue
            denom += 1
            if t in hay:
                hits += 1
        overlap = hits / max(1, denom)

    hint_bonus = 0.0
    if diagnosis_hint:
        h = diagnosis_hint.lower().strip()
        if h and (h in src or h in title or h in url or h in text[:3000].lower()):
            hint_bonus = 0.35
    return min(1.5, overlap + hint_bonus)

def _dedupe_key(d: Dict) -> Tuple[str, str, str]:
    url = (d.get("url") or "").strip()
    src = (d.get("source") or "").strip()
    txt = (d.get("text") or "").strip()[:200]
    return (url, src, txt)

def _ensure_loaded():
    global _model, _index, _DOCS
    if CALLCARE_DISABLE_FAISS:
        return
    if _model is None:
        _model = get_embedder()
    if _index is None:
        if not FAISS_PATH.exists():
            raise FileNotFoundError(f"FAISS index not found: {FAISS_PATH}")
        _index = faiss.read_index(str(FAISS_PATH))
    if _DOCS is None:
        if not DOCS_PATH.exists():
            raise FileNotFoundError(f"Docs file not found: {DOCS_PATH}")
        with open(DOCS_PATH, "r", encoding="utf-8") as f:
            _DOCS = json.load(f)

def _retrieve_impl(query: str, k: int = 5, *, diagnosis_hint: Optional[str] = None, fetch_k: int = 40):
    if CALLCARE_DISABLE_FAISS:
        return []

    _ensure_loaded()

    query = "" if query is None else str(query).strip()
    if not query:
        return []

    qv = _model.encode([query]).astype("float32")
    faiss.normalize_L2(qv)

    fetch_k = max(int(fetch_k), int(k), 10)
    scores, ids = _index.search(qv, int(fetch_k))

    candidates: List[Dict] = []
    for score, idx in zip(scores[0], ids[0]):
        try:
            d = _DOCS[int(idx)]
        except Exception:
            continue
        candidates.append({
            "score": float(score),
            "source": d.get("source", "") or "",
            "title": d.get("title", "") or "Guideline excerpt",
            "url": d.get("url", "") or "",
            "text": d.get("text", "") or "",
        })

    seen = set()
    deduped: List[Dict] = []
    for d in candidates:
        key = _dedupe_key(d)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(d)

    q_tokens = _tokenize(query)
    scored: List[Tuple[float, Dict]] = []
    for d in deduped:
        rel = _relevance_score(q_tokens, d, diagnosis_hint)
        combined = d["score"] + (0.75 * rel)
        scored.append((combined, d))
    scored.sort(key=lambda x: x[0], reverse=True)

    filtered: List[Dict] = []
    for combined, d in scored:
        rel = _relevance_score(q_tokens, d, diagnosis_hint)
        if rel >= 0.12 or (diagnosis_hint and rel >= 0.30):
            filtered.append(d)
        if len(filtered) >= int(k):
            break

    if len(filtered) < int(min(k, 3)):
        filtered = [d for _, d in scored[: int(k)]]

    if _debug_enabled():
        print("\n=== RAG_DEBUG retrieve() ===")
        print(f"CALLCARE_DISABLE_FAISS={CALLCARE_DISABLE_FAISS}")
        print(f"query: {query!r}")
        print(f"diagnosis_hint: {diagnosis_hint!r}")
        print(f"requested k: {k}  fetch_k: {fetch_k}")
        print(f"candidates: {len(candidates)}  deduped: {len(deduped)}  returned: {len(filtered)}")
        for i, d in enumerate(filtered[: min(len(filtered), 10)], start=1):
            snippet = (d.get("text") or "").replace("\n", " ").strip()[:180]
            print(f"{i:02d}. score={d['score']:.4f}  title={d.get('title','')[:80]!r}")
            print(f"    url={d.get('url','')[:120]!r}")
            print(f"    snippet={snippet!r}")
        print("=== END RAG_DEBUG ===\n")

    return filtered

def retrieve(query, *args, **kwargs):
    """
    Wrapper around _retrieve_impl that logs query + top hits to logs/retrieve.jsonl
    """
    hits = []
    try:
        hits = _retrieve_impl(query, *args, **kwargs)
    except Exception as e:
        try:
            import time
            os.makedirs("logs", exist_ok=True)
            with open("logs/retrieve.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "ok": False, "query": str(query), "error": str(e)}, default=str) + "\n")
        except Exception:
            pass
        raise

    try:
        import time
        os.makedirs("logs", exist_ok=True)
        top = []
        if isinstance(hits, list):
            for h in hits[:10]:
                if isinstance(h, dict):
                    top.append({"title": (h.get("title") or "")[:120], "url": (h.get("url") or h.get("source") or "")[:240]})
                else:
                    top.append({"repr": str(h)[:240]})
        with open("logs/retrieve.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "ok": True, "query": str(query), "top": top}, default=str) + "\n")
    except Exception:
        pass

    return hits
