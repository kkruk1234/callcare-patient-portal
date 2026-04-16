#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

FINALIZE = Path("app/clinical/finalize.py")

NEW_BLOCK = r'''
def _rebuild_evidence_from_differential(note, state, max_total=6, min_total=3):
    """
    SAFETY VERSION (no-regression):
    - Rebuild Evidence Used guided by differential, BUT:
      1) Never reduce the evidence list (merge with existing note.evidence).
      2) Guarantee at least min_total items if possible.
      3) Avoid generic/rule-out items dominating when specific items exist.
    - No hard-coded diagnoses. Uses differential text only.
    """

    try:
        from app.rag.retrieve import retrieve
        from app.clinical.evidence_adapter import as_evidence_refs
    except Exception:
        return note

    # Start from whatever evidence note_builder already produced (important!)
    existing = list(getattr(note, "evidence", None) or [])
    # Normalize existing into EvidenceRefs if needed
    try:
        existing_refs = as_evidence_refs(existing) or existing
    except Exception:
        existing_refs = existing

    diff = _extract_differential_from_note(note) or []
    cc = str(getattr(state, "chief_complaint", "") or "").strip()

    # If no differential, do not destroy existing evidence
    if not diff:
        # still enforce minimum count if possible
        if len(existing_refs) >= min_total:
            note.evidence = existing_refs[:max_total]
            return note
        # else try a generic top-up
        q = (cc + " guideline treatment management outpatient").strip()
        try:
            chunks = retrieve(q, k=50) or []
        except TypeError:
            chunks = retrieve(q) or []
        try:
            top = as_evidence_refs(chunks) or []
        except Exception:
            top = []
        merged = _cc__merge_evidence_refs(existing_refs, top, max_total=max_total)
        note.evidence = merged[:max_total]
        return note

    hedge_markers = ("less likely", "unlikely", "rule out", "ruled out", "low likelihood", "cannot exclude")
    generic_markers = ("cardiac", "pulmonary", "serious", "life-threatening", "etiology", "differential", "causes")

    def _is_hedged(item: str) -> bool:
        it = (item or "").lower()
        return any(h in it for h in hedge_markers)

    def _is_generic(item: str) -> bool:
        it = (item or "").lower()
        if any(g in it for g in generic_markers):
            words = [w for w in it.replace("/", " ").replace("-", " ").split() if w]
            return len(words) <= 4
        return False

    def _item_tokens(item: str):
        it = (item or "").lower()
        toks = [t for t in it.replace("/", " ").replace("-", " ").split() if len(t) >= 5]
        stop = set(["likely","less","unlikely","possible","rule","ruled","exclude","cannot","causes","cause","etiology","differential","presentation"])
        toks = [t for t in toks if t not in stop]
        return toks[:8]

    preferred_domains = ("nhs.uk/conditions/", "nice", "cdc.gov", "nih.gov", "niddk.nih.gov", "aafp.org", "ncbi.nlm.nih.gov", "mayoclinic.org", "aapmr.org")

    def _chunk_fields(ch):
        if isinstance(ch, dict):
            title = (ch.get("title") or "")
            url = (ch.get("url") or ch.get("source") or "")
            text = (ch.get("text") or "")
            return str(title), str(url), str(text)
        title = getattr(ch, "title", "") or ""
        url = getattr(ch, "url", "") or getattr(ch, "source", "") or ""
        text = getattr(ch, "text", "") or ""
        return str(title), str(url), str(text)

    def _score_chunk_for_item(ch, item: str) -> int:
        title, url, text = _chunk_fields(ch)
        title_l = title.lower()
        url_l = url.lower()
        text_l = text.lower()
        blob = (title_l + " " + url_l + " " + text_l)

        it = (item or "").lower()
        toks = _item_tokens(it)

        s = 0
        if any(d in url_l for d in preferred_domains):
            s += 10
        if "nhs.uk/conditions/" in url_l:
            s += 25
        if "nice" in blob or "guideline" in blob:
            s += 10
        if toks and any(t in blob for t in toks[:4]):
            s += 25
        if "/lab-tests/" in url_l:
            s -= 25
        if url_l.endswith("/chestpain.html") or title_l.strip() == "chest pain | medlineplus":
            s -= 30

        # Generic/rule-out items must have token overlap or they drop
        if _is_generic(it) or _is_hedged(it):
            if not (toks and any(t in blob for t in toks[:4])):
                s -= 50

        return s

    picked_chunks = []
    seen_urls = set()

    def _pick_for_item(item: str):
        nonlocal picked_chunks, seen_urls
        for q in _diff_item_to_queries(item, cc):
            try:
                chunks = retrieve(q, k=50) or []
            except TypeError:
                chunks = retrieve(q) or []
            # keep dicts only (retrieve returns dict chunks in this codebase)
            chunks = [c for c in chunks if isinstance(c, dict)]
            chunks.sort(key=lambda c: _score_chunk_for_item(c, item), reverse=True)
            for ch in chunks[:25]:
                url = (ch.get("url") or ch.get("source") or "").strip()
                title = (ch.get("title") or "").strip()
                if not url or url in seen_urls:
                    continue
                try:
                    ok = _should_allow_dx_url(item, title, url, ch.get("text") or "")
                except Exception:
                    ok = True
                if not ok:
                    continue
                seen_urls.add(url)
                picked_chunks.append(ch)
                return

    # PASS A: pick for specific items first
    for item in diff:
        if len(picked_chunks) >= max_total:
            break
        if _is_generic(item) or _is_hedged(item):
            continue
        _pick_for_item(item)

    # PASS B: only if still below minimum
    for item in diff:
        if len(picked_chunks) >= max_total:
            break
        if len(picked_chunks) >= min_total:
            break
        if not (_is_generic(item) or _is_hedged(item)):
            continue
        _pick_for_item(item)

    # Convert picked chunks to EvidenceRefs
    try:
        rebuilt_refs = as_evidence_refs(picked_chunks) or []
    except Exception:
        rebuilt_refs = []

    # Merge with existing evidence so we NEVER reduce the list
    merged = _cc__merge_evidence_refs(existing_refs, rebuilt_refs, max_total=max_total)

    # If still < min_total, top up using the primary differential item
    if len(merged) < min_total:
        primary = diff[0]
        q = (primary + " treatment management outpatient guideline").strip()
        try:
            chunks = retrieve(q, k=80) or []
        except TypeError:
            chunks = retrieve(q) or []
        try:
            top_refs = as_evidence_refs(chunks) or []
        except Exception:
            top_refs = []
        merged = _cc__merge_evidence_refs(merged, top_refs, max_total=max_total)

    # Finally enforce min_total if possible (but do not exceed max_total)
    note.evidence = merged[:max_total]
    return note


def _cc__merge_evidence_refs(a, b, max_total=6):
    """
    Merge two lists of EvidenceRef-like objects with de-duplication by URL (preferred) then by title.
    This lives in finalize.py to avoid importing note_builder and risking cycles.
    """
    out = []
    seen = set()

    def key(ev):
        url = str(getattr(ev, "url", "") or "").strip()
        if not url:
            url = str(getattr(ev, "source", "") or "").strip()
        title = str(getattr(ev, "title", "") or "").strip()
        if url:
            return ("url:" + url).lower()
        if title:
            return ("title:" + title).lower()
        return ("repr:" + repr(ev)).lower()

    for lst in (a or [], b or []):
        if not isinstance(lst, list):
            continue
        for ev in lst:
            if ev is None:
                continue
            k = key(ev)
            if k in seen:
                continue
            seen.add(k)
            out.append(ev)
            if len(out) >= max_total:
                return out
    return out
'''

def main():
    if not FINALIZE.exists():
        print(f"ERROR: missing {FINALIZE}")
        return 2

    txt = FINALIZE.read_text(encoding="utf-8")

    start_key = "def _rebuild_evidence_from_differential("
    end_key = "def _gate_rx_candidates_by_note_text("

    s = txt.find(start_key)
    e = txt.find(end_key)

    if s == -1 or e == -1 or e <= s:
        print("ERROR: Could not locate function block to replace.")
        print(f"Found start={s} end={e}")
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = FINALIZE.with_suffix(FINALIZE.suffix + f".bak_EVIDREBUILD_V2_{ts}")
    backup.write_text(txt, encoding="utf-8")

    new_txt = txt[:s] + NEW_BLOCK.lstrip("\n") + "\n\n" + txt[e:]
    FINALIZE.write_text(new_txt, encoding="utf-8")

    print(f"OK: Patched {FINALIZE}")
    print(f"Backup: {backup}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
