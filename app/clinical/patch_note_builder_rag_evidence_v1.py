#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

NB = Path("app/clinical/note_builder.py")

def replace_def_block(txt: str, def_name: str, new_block: str) -> str:
    key = f"def {def_name}("
    s = txt.find(key)
    if s == -1:
        raise RuntimeError(f"Could not find {def_name} definition")
    n = txt.find("\ndef ", s + 1)
    if n == -1:
        n = len(txt)
    return txt[:s] + new_block.rstrip() + "\n\n" + txt[n+1:]

NEW_EVIDENCE_FROM_RAG = r'''
def _evidence_from_rag(state: CallState, max_snips: int = 6) -> List[EvidenceRef]:
    """
    CallCare RAG evidence (generalist):
      - Build a condition/complaint query from pathway_id + chief complaint.
      - Strip pregnancy/sex tokens from the query (to avoid 'women', 'Turner syndrome', etc.).
      - Prefer outpatient management domains.
      - Normalize local library chunk references to real (title,url) via nearest source.json.
    """
    try:
        from app.rag.retrieve import retrieve
    except Exception:
        return []

    # --- Build query: pathway + chief complaint (diagnosis-forward) ---
    pid = str(getattr(state, "pathway_id", "") or "").strip().replace("_", " ")
    cc = str(getattr(state, "chief_complaint", "") or "").strip()

    base = (pid + " " + cc).strip()

    # Remove tokens that cause irrelevant demographic/genetic pulls
    bad = (
        "pregnant", "pregnancy", "trimester", "lmp",
        "sex at birth", "assigned at birth", "male", "female",
        "woman", "women", "man", "men", "girl", "boy",
        "turner", "syndrome",
    )
    base_l = base.lower()
    for b in bad:
        if b in base_l:
            # simple removal without regex
            base_l = base_l.replace(b, " ")
    # collapse whitespace
    base = " ".join(base_l.split())

    # Make it outpatient/treatment oriented
    query = (base + " outpatient treatment management guideline").strip()
    if not query:
        query = "outpatient treatment management guideline"

    preferred_domains = (
        "aafp.org",
        "ncbi.nlm.nih.gov",
        "mayoclinic.org",
        "nhs.uk",
        "nice.org.uk",
        "cdc.gov",
        "nih.gov",
        "aapmr.org",
    )

    def _domain_score(url: str) -> int:
        u = (url or "").lower()
        for i, d in enumerate(preferred_domains):
            if d in u:
                # earlier domains slightly higher
                return 30 - i
        return 0

    # Normalize library chunk -> source.json URL/title
    def _normalize_chunk(c: dict) -> dict:
        if not isinstance(c, dict):
            return c
        title = str(c.get("title") or c.get("name") or "").strip()
        url = str(c.get("url") or c.get("source") or "").strip()

        def looks_local(u: str) -> bool:
            if not u:
                return True
            if u.startswith("http://") or u.startswith("https://"):
                return False
            if "library/" in u or u.endswith("content.txt") or u.endswith(".json"):
                return True
            return True

        if looks_local(url):
            # Try resolving relative to data/sources/
            from pathlib import Path
            import json
            p = Path(url)
            if not p.is_absolute():
                p2 = Path("data/sources") / p
                if p2.exists():
                    p = p2
            cur = p.parent
            sj = None
            for _ in range(6):
                cand = cur / "source.json"
                if cand.exists():
                    sj = cand
                    break
                cur = cur.parent
            if sj is not None:
                try:
                    meta = json.loads(sj.read_text(encoding="utf-8"))
                    real_url = str(meta.get("url") or "").strip()
                    real_title = str(meta.get("title") or "").strip()
                    if real_url.startswith(("http://", "https://")):
                        c = dict(c)
                        c["url"] = real_url
                        c["source"] = real_url
                        if not title or title.lower() in ("content.txt", "source.json"):
                            c["title"] = real_title or title or "Source"
                except Exception:
                    pass
        return c

    # Retrieve overfetch then rank ourselves
    try:
        chunks = retrieve(query, k=60) or []
    except TypeError:
        chunks = retrieve(query) or []

    norm = []
    for ch in chunks:
        if not isinstance(ch, dict):
            continue
        ch2 = _normalize_chunk(ch)
        # Require real URL; otherwise it's not citeable
        u = str(ch2.get("url") or ch2.get("source") or "").strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            continue
        norm.append(ch2)

    # Rank: domain preference + token overlap with pathway/cc
    tokens = []
    for w in (pid + " " + cc).lower().replace("_", " ").split():
        w = w.strip()
        if len(w) >= 5 and w not in ("possible", "likely", "unlikely", "today"):
            tokens.append(w)
    tokens = tokens[:10]

    def score(ch: dict) -> int:
        title = str(ch.get("title") or "").lower()
        url = str(ch.get("url") or ch.get("source") or "").lower()
        blob = title + " " + url
        s = _domain_score(url)
        # token overlap
        hits = 0
        for t in tokens[:6]:
            if t and t in blob:
                hits += 1
        s += hits * 20
        # penalize ultra-generic hubs
        if "medlineplus.gov" in url and ("chestpain" in url or "chest" in url):
            s -= 25
        return s

    norm.sort(key=score, reverse=True)

    # Dedup by URL, convert to EvidenceRef
    seen = set()
    refs: List[EvidenceRef] = []
    for ch in norm:
        if len(refs) >= max_snips:
            break
        u = str(ch.get("url") or ch.get("source") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        try:
            ev = _mk_evidence_ref(ch)
        except Exception:
            continue
        refs.append(ev)

    return refs
'''

def main() -> int:
    if not NB.exists():
        print(f"ERROR: missing {NB}")
        return 2

    txt = NB.read_text(encoding="utf-8")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = NB.with_suffix(NB.suffix + f".bak_RAGFIX_{ts}")
    bak.write_text(txt, encoding="utf-8")

    try:
        txt2 = replace_def_block(txt, "_evidence_from_rag", NEW_EVIDENCE_FROM_RAG)
    except Exception as e:
        print("ERROR:", e)
        return 2

    NB.write_text(txt2, encoding="utf-8")
    print(f"OK: patched {NB}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
