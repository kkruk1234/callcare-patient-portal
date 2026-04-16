#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

FINALIZE = Path("app/clinical/finalize.py")

NEW_BLOCK = r'''
def _rebuild_evidence_from_differential(note, state, max_total=6, min_total=3):
    """
    SAFETY VERSION v3 (no-regression + relevance rerank + dosing anchor):
    - Never reduce evidence (merge with existing note.evidence).
    - Guarantee at least min_total items when possible.
    - Rerank/prune by overlap with PRIMARY differential item tokens so off-topic rule-out pages
      (e.g. pericarditis) do not appear when we have enough on-topic evidence.
    - If plan mentions common OTC analgesics, ensure at least one dosing-capable anchor is present.
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

    preferred_domains = ("nhs.uk/conditions/", "nice", "cdc.gov", "nih.gov", "niddk.nih.gov", "aafp.org", "ncbi.nlm.nih.gov", "mayoclinic.org", "aapmr.org")

    def _ev_fields(ev):
        # EvidenceRef-like
        title = str(getattr(ev, "title", "") or "").strip()
        url = str(getattr(ev, "url", "") or "").strip()
        if not url:
            url = str(getattr(ev, "source", "") or "").strip()
        text = ""
        return title, url, text

    def _chunk_fields(ch):
        title = str((ch.get("title") or "")).strip()
        url = str((ch.get("url") or ch.get("source") or "")).strip()
        text = str((ch.get("text") or "")).strip()
        return title, url, text

    def _score_blob(blob: str, url_l: str) -> int:
        s = 0
        if any(d in url_l for d in preferred_domains):
            s += 8
        if "nhs.uk/conditions/" in url_l:
            s += 15
        if "guideline" in blob or "nice" in blob:
            s += 6
        return s

    def _score_ev_for_primary(ev, primary_tokens):
        title, url, _ = _ev_fields(ev)
        blob = (title + " " + url).lower()
        url_l = url.lower()
        s = _score_blob(blob, url_l)
        hits = 0
        for t in primary_tokens[:6]:
            if t in blob:
                hits += 1
        s += hits * 20
        return s, hits

    def _score_chunk_for_item(ch, item: str) -> int:
        title, url, text = _chunk_fields(ch)
        blob = (title + " " + url + " " + text).lower()
        url_l = url.lower()
        it = (item or "").lower()
        toks = _item_tokens(it)

        s = _score_blob(blob, url_l)
        if toks and any(t in blob for t in toks[:4]):
            s += 25

        if "/lab-tests/" in url_l:
            s -= 25
        if url_l.endswith("/chestpain.html") or title.lower().strip() == "chest pain | medlineplus":
            s -= 30

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

    # If no differential: keep existing evidence and top-up generically
    if not diff:
        merged = _cc__merge_evidence_refs(existing_refs, [], max_total=max_total)
        if len(merged) < min_total:
            q = (cc + " guideline treatment management outpatient").strip()
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

    # PASS A: specific items first
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

    try:
        rebuilt_refs = as_evidence_refs(picked_chunks) or []
    except Exception:
        rebuilt_refs = []

    merged = _cc__merge_evidence_refs(existing_refs, rebuilt_refs, max_total=max_total)

    # Top up for primary differential item until we have min_total
    primary_item = diff[0]
    primary_tokens = _item_tokens(primary_item)

    if len(merged) < min_total:
        q = (primary_item + " treatment management outpatient guideline").strip()
        try:
            chunks = retrieve(q, k=120) or []
        except TypeError:
            chunks = retrieve(q) or []
        try:
            top_refs = as_evidence_refs(chunks) or []
        except Exception:
            top_refs = []
        merged = _cc__merge_evidence_refs(merged, top_refs, max_total=max_total)

    # RERANK by overlap with primary differential tokens, and DROP zero-overlap items
    # if we already have enough on-topic evidence.
    if primary_tokens:
        scored = []
        for ev in merged:
            s, hits = _score_ev_for_primary(ev, primary_tokens)
            scored.append((s, hits, ev))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

        # Count how many have at least 1 hit
        hit_count = sum(1 for _, hits, _ in scored if hits >= 1)

        new_list = [ev for _, _, ev in scored]

        # If we have enough on-topic evidence, remove items with 0 hits (this drops pericarditis for costochondritis)
        if hit_count >= min_total:
            keep = []
            for ev in new_list:
                _, hits = _score_ev_for_primary(ev, primary_tokens)
                if hits >= 1:
                    keep.append(ev)
                if len(keep) >= max_total:
                    break
            # If keep is still < min_total (rare), fall back to top-ranked list
            if len(keep) >= min_total:
                merged = keep
            else:
                merged = new_list[:max_total]
        else:
            merged = new_list[:max_total]

    # DOSING ANCHOR: if plan mentions common OTC analgesics, ensure we include a dosing-capable source
    soap = (getattr(note, "soap", "") or "").lower()
    wants_otc = any(w in soap for w in ["acetaminophen", "ibuprofen", "naproxen", "nsaid"])
    if wants_otc:
        # Check if we already have a dosing-ish anchor
        have_anchor = False
        for ev in merged:
            title, url, _ = _ev_fields(ev)
            blob = (title + " " + url).lower()
            if "pharmacologic therapy for acute pain" in blob or "/p63.html" in blob:
                have_anchor = True
                break
        if not have_anchor and len(merged) < max_total:
            q = "Pharmacologic Therapy for Acute Pain acetaminophen ibuprofen dosing AAFP"
            try:
                chunks = retrieve(q, k=80) or []
            except TypeError:
                chunks = retrieve(q) or []
            chunks = [c for c in chunks if isinstance(c, dict)]
            # pick first aafp hit
            picked = None
            for c in chunks:
                url = str(c.get("url") or c.get("source") or "").lower()
                title = str(c.get("title") or "").lower()
                if "aafp.org" in url and ("acute pain" in title or "/p63.html" in url):
                    picked = c
                    break
            if picked:
                try:
                    add_refs = as_evidence_refs([picked]) or []
                except Exception:
                    add_refs = []
                merged = _cc__merge_evidence_refs(merged, add_refs, max_total=max_total)

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
    backup = FINALIZE.with_suffix(FINALIZE.suffix + f".bak_EVIDREBUILD_V3_{ts}")
    backup.write_text(txt, encoding="utf-8")

    new_txt = txt[:s] + NEW_BLOCK.lstrip("\n") + "\n\n" + txt[e:]
    FINALIZE.write_text(new_txt, encoding="utf-8")

    print(f"OK: Patched {FINALIZE}")
    print(f"Backup: {backup}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
