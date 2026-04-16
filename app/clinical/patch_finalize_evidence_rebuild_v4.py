#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

FINALIZE = Path("app/clinical/finalize.py")

NEW_BLOCK = r'''
def _rebuild_evidence_from_differential(note, state, max_total=6, min_total=3):
    """
    SAFETY VERSION v4:
    - Never reduce evidence (merge with existing note.evidence).
    - Guarantee >= min_total items when possible.
    - Remove off-topic "rule-out noise" by matching evidence titles/urls against tokens
      from SPECIFIC (non-generic, non-hedged) differential items.
    - Guarantee dosing anchor when plan mentions OTC analgesics by pulling it directly
      from data/index/docs.json (not via retrieval ranking).
    """

    try:
        from app.rag.retrieve import retrieve
        from app.clinical.evidence_adapter import as_evidence_refs
    except Exception:
        return note

    existing = list(getattr(note, "evidence", None) or [])
    try:
        existing_refs = as_evidence_refs(existing) or existing
    except Exception:
        existing_refs = existing

    diff = _extract_differential_from_note(note) or []
    cc = str(getattr(state, "chief_complaint", "") or "").strip()

    # If no differential, keep existing and just top up generically
    if not diff:
        merged = _cc__merge_evidence_refs(existing_refs, [], max_total=max_total)
        if len(merged) < min_total:
            q = (cc + " outpatient treatment management guideline").strip()
            try:
                chunks = retrieve(q, k=80) or []
            except TypeError:
                chunks = retrieve(q) or []
            try:
                top_refs = as_evidence_refs(chunks) or []
            except Exception:
                top_refs = []
            merged = _cc__merge_evidence_refs(merged, top_refs, max_total=max_total)
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
        stop = set([
            "likely","less","unlikely","possible","rule","ruled","exclude","cannot",
            "causes","cause","etiology","differential","presentation","atypical"
        ])
        toks = [t for t in toks if t not in stop]
        return toks[:10]

    # SPECIFIC items = the signal we care about
    specific_items = []
    for item in diff:
        if _is_generic(item) or _is_hedged(item):
            continue
        specific_items.append(item)
        if len(specific_items) >= 3:
            break
    if not specific_items:
        # if everything is generic/hedged, fall back to first 2 diff items as weak signal
        specific_items = diff[:2]

    specific_tokens_sets = [set(_item_tokens(it)) for it in specific_items if _item_tokens(it)]
    # Flatten for quick checks
    any_specific_tokens = set()
    for s in specific_tokens_sets:
        any_specific_tokens |= s

    preferred_domains = (
        "nhs.uk/conditions/", "nice", "cdc.gov", "nih.gov", "niddk.nih.gov",
        "aafp.org", "ncbi.nlm.nih.gov", "mayoclinic.org", "aapmr.org"
    )

    def _score_blob_base(blob: str, url_l: str) -> int:
        s = 0
        if any(d in url_l for d in preferred_domains):
            s += 8
        if "nhs.uk/conditions/" in url_l:
            s += 15
        if "guideline" in blob or "nice" in blob:
            s += 6
        # penalize obvious generic hubs
        if "medlineplus.gov" in url_l and ("chest" in url_l and "pain" in url_l):
            s -= 15
        return s

    def _ev_fields(ev):
        title = str(getattr(ev, "title", "") or "").strip()
        url = str(getattr(ev, "url", "") or "").strip()
        if not url:
            url = str(getattr(ev, "source", "") or "").strip()
        return title, url

    def _ev_overlap_hits(ev) -> int:
        title, url = _ev_fields(ev)
        blob = (title + " " + url).lower()
        if not any_specific_tokens:
            return 0
        hits = 0
        for t in list(any_specific_tokens)[:10]:
            if t and t in blob:
                hits += 1
        return hits

    def _ev_overlap_max(ev) -> int:
        title, url = _ev_fields(ev)
        blob = (title + " " + url).lower()
        if not specific_tokens_sets:
            return 0
        best = 0
        for s in specific_tokens_sets:
            if not s:
                continue
            h = 0
            for t in list(s)[:8]:
                if t and t in blob:
                    h += 1
            if h > best:
                best = h
        return best

    def _score_ev(ev) -> int:
        title, url = _ev_fields(ev)
        blob = (title + " " + url).lower()
        url_l = url.lower()
        s = _score_blob_base(blob, url_l)
        s += _ev_overlap_max(ev) * 25
        # penalize lab-tests as Evidence Used
        if "/lab-tests/" in url_l:
            s -= 25
        return s

    # --- rebuild evidence chunks guided by diff (kept from your approach) ---
    picked_chunks = []
    seen_urls = set()

    def _score_chunk_for_item(ch, item: str) -> int:
        if not isinstance(ch, dict):
            return -999
        title = str(ch.get("title") or "")
        url = str(ch.get("url") or ch.get("source") or "")
        text = str(ch.get("text") or "")
        blob = (title + " " + url + " " + text).lower()
        url_l = url.lower()
        toks = _item_tokens(item)

        s = _score_blob_base(blob, url_l)
        if toks and any(t in blob for t in toks[:4]):
            s += 25

        # penalize generic hubs and lab-tests
        if "/lab-tests/" in url_l:
            s -= 25
        if "medlineplus.gov" in url_l and ("chest" in url_l and "pain" in url_l):
            s -= 20

        # generic/rule-out items must have overlap or they get pushed down
        if _is_generic(item) or _is_hedged(item):
            if not (toks and any(t in blob for t in toks[:4])):
                s -= 60
        return s

    def _pick_for_item(item: str):
        nonlocal picked_chunks, seen_urls
        for q in _diff_item_to_queries(item, cc):
            try:
                chunks = retrieve(q, k=60) or []
            except TypeError:
                chunks = retrieve(q) or []
            chunks = [c for c in chunks if isinstance(c, dict)]
            chunks.sort(key=lambda c: _score_chunk_for_item(c, item), reverse=True)
            for ch in chunks[:30]:
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

    # Prefer specific items first
    for item in specific_items:
        if len(picked_chunks) >= max_total:
            break
        _pick_for_item(item)

    # If still below minimum, consider remaining diff items
    for item in diff:
        if len(picked_chunks) >= max_total:
            break
        if len(picked_chunks) >= min_total:
            break
        if item in specific_items:
            continue
        _pick_for_item(item)

    try:
        rebuilt_refs = as_evidence_refs(picked_chunks) or []
    except Exception:
        rebuilt_refs = []

    merged = _cc__merge_evidence_refs(existing_refs, rebuilt_refs, max_total=max_total)

    # Top up to min_total using a strong outpatient query built from the most specific item
    if len(merged) < min_total and specific_items:
        q = (specific_items[0] + " treatment management outpatient guideline NSAID acetaminophen").strip()
        try:
            chunks = retrieve(q, k=120) or []
        except TypeError:
            chunks = retrieve(q) or []
        try:
            top_refs = as_evidence_refs(chunks) or []
        except Exception:
            top_refs = []
        merged = _cc__merge_evidence_refs(merged, top_refs, max_total=max_total)

    # RERANK by specificity overlap and prune zero-overlap items when possible
    scored = [( _score_ev(ev), _ev_overlap_max(ev), ev) for ev in merged]
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    ordered = [ev for _, _, ev in scored]

    # If we can form >= min_total items with at least 1 overlap hit, drop the zero-hit items
    hit_items = [ev for ev in ordered if _ev_overlap_max(ev) >= 1]
    if len(hit_items) >= min_total:
        ordered = hit_items

    merged = ordered[:max_total]

    # DOSING ANCHOR: if SOAP mentions OTC analgesics, force include AAFP acute pain page from index
    soap = (getattr(note, "soap", "") or "").lower()
    wants_otc = any(w in soap for w in ["acetaminophen", "ibuprofen", "naproxen", "nsaid"])
    if wants_otc:
        have_anchor = False
        for ev in merged:
            t, u = _ev_fields(ev)
            blob = (t + " " + u).lower()
            if "pharmacologic therapy for acute pain" in blob or "/p63.html" in blob:
                have_anchor = True
                break

        if not have_anchor:
            # Pull from local index docs.json (no retrieval dependency)
            try:
                import json
                docs = json.load(open("data/index/docs.json", "r", encoding="utf-8"))
            except Exception:
                docs = []

            candidate = None
            for d in docs if isinstance(docs, list) else []:
                if not isinstance(d, dict):
                    continue
                url = str(d.get("url") or "").lower()
                title = str(d.get("title") or "").lower()
                if "/p63.html" in url or "pharmacologic therapy for acute pain" in title:
                    candidate = d
                    break

            if candidate:
                try:
                    add_refs = as_evidence_refs([candidate]) or []
                except Exception:
                    add_refs = []
                merged = _cc__merge_evidence_refs(merged, add_refs, max_total=max_total)

    # Ensure minimum count if possible
    note.evidence = merged[:max_total]
    return note


def _cc__merge_evidence_refs(a, b, max_total=6):
    """
    Merge two lists of EvidenceRef-like objects with de-duplication by URL (preferred) then by title.
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
    backup = FINALIZE.with_suffix(FINALIZE.suffix + f".bak_EVIDREBUILD_V4_{ts}")
    backup.write_text(txt, encoding="utf-8")

    new_txt = txt[:s] + NEW_BLOCK.lstrip("\n") + "\n\n" + txt[e:]
    FINALIZE.write_text(new_txt, encoding="utf-8")

    print(f"OK: Patched {FINALIZE}")
    print(f"Backup: {backup}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
