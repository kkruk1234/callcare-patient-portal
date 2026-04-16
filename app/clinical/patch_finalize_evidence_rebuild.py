#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

FINALIZE = Path("app/clinical/finalize.py")

NEW_BLOCK = r'''
def _rebuild_evidence_from_differential(note, state, max_total=6, min_total=3):
    """
    Build Evidence Used to match the NOTE differential in order, BUT:
      - Do not let generic/rule-out differential items (e.g. "cardiac causes") dominate Evidence Used.
      - Guarantee at least min_total evidence items when corpus contains them.
      - No hard-coded diseases. Uses only differential text + overlap scoring.

    Strategy:
      1) Extract differential items from note.
      2) Two-pass selection:
         A) Prefer non-hedged, non-generic items first (the working diagnosis / likely differentials)
         B) Only then consider generic / rule-out items if we still need more evidence.
      3) If still < min_total, top up with additional distinct sources for the first (most likely) differential item.
    """
    try:
        from app.rag.retrieve import retrieve
        from app.clinical.evidence_adapter import as_evidence_refs
    except Exception:
        return note

    diff = _extract_differential_from_note(note)
    if not diff:
        return note

    cc = str(getattr(state, "chief_complaint", "") or "").strip()

    # Generic/rule-out markers (generalizable; not diagnosis-specific)
    hedge_markers = ("less likely", "unlikely", "rule out", "ruled out", "low likelihood", "cannot exclude")
    generic_markers = ("cardiac", "pulmonary", "serious", "life-threatening", "etiology", "differential", "causes")

    def _is_hedged(item: str) -> bool:
        it = (item or "").lower()
        return any(h in it for h in hedge_markers)

    def _is_generic(item: str) -> bool:
        it = (item or "").lower()
        # short generic lines like "cardiac causes" shouldn't drive evidence
        if any(g in it for g in generic_markers):
            # treat as generic when short / non-specific
            words = [w for w in it.replace("/", " ").replace("-", " ").split() if w]
            return len(words) <= 4
        return False

    def _item_tokens(item: str):
        it = (item or "").lower()
        # keep meaningful tokens; avoid tiny words
        toks = [t for t in it.replace("/", " ").replace("-", " ").split() if len(t) >= 5]
        # de-noise common hedge words
        stop = set(["likely","less","unlikely","possible","rule","ruled","exclude","cannot","causes","cause","etiology","differential","presentation"])
        toks = [t for t in toks if t not in stop]
        return toks[:8]

    preferred_domains = ("nhs.uk/conditions/", "nice", "cdc.gov", "nih.gov", "niddk.nih.gov", "aafp.org", "ncbi.nlm.nih.gov", "mayoclinic.org", "aapmr.org")

    def _score_chunk_for_item(ch: dict, item: str) -> int:
        title = (ch.get("title") or "").lower()
        url = (ch.get("url") or ch.get("source") or "").lower()
        text = (ch.get("text") or "").lower()
        blob = title + " " + url + " " + text

        it = (item or "").lower()
        toks = _item_tokens(it)

        s = 0
        # prefer credible domains
        if any(d in url for d in preferred_domains):
            s += 10
        if "nhs.uk/conditions/" in url:
            s += 25
        if "nice" in blob or "guideline" in blob:
            s += 10

        # token overlap
        if toks and any(t in blob for t in toks[:4]):
            s += 25

        # penalize lab-test pages as Evidence Used
        if "/lab-tests/" in url:
            s -= 25

        # penalize generic hubs
        if url.endswith("/chestpain.html") or title.strip() == "chest pain | medlineplus":
            s -= 30

        # CRITICAL: generic/rule-out items must have token overlap or they get pushed down hard
        if _is_generic(it) or _is_hedged(it):
            if not (toks and any(t in blob for t in toks[:4])):
                s -= 50

        return s

    picked = []
    seen_urls = set()

    def _pick_for_item(item: str):
        nonlocal picked, seen_urls
        # Build queries from existing helper (keeps your current behavior)
        for q in _diff_item_to_queries(item, cc):
            try:
                chunks = retrieve(q, k=40) or []
            except TypeError:
                chunks = retrieve(q) or []
            chunks = [c for c in chunks if isinstance(c, dict)]
            chunks.sort(key=lambda c: _score_chunk_for_item(c, item), reverse=True)

            for ch in chunks[:20]:
                url = (ch.get("url") or ch.get("source") or "").strip()
                title = (ch.get("title") or "").strip()
                if not url or url in seen_urls:
                    continue
                # existing allow gate (keeps your rules), but generic items will already be de-scored above
                try:
                    ok = _should_allow_dx_url(item, title, url, ch.get("text") or "")
                except Exception:
                    ok = True
                if not ok:
                    continue
                seen_urls.add(url)
                picked.append(ch)
                return

    # PASS A: non-generic, non-hedged first
    for item in diff:
        if len(picked) >= max_total:
            break
        if _is_generic(item) or _is_hedged(item):
            continue
        _pick_for_item(item)

    # PASS B: only if still need more
    for item in diff:
        if len(picked) >= max_total:
            break
        if not (_is_generic(item) or _is_hedged(item)):
            continue
        # only allow rule-out/generic evidence if we haven't met the minimum yet
        if len(picked) >= min_total:
            continue
        _pick_for_item(item)

    # TOP UP: ensure >= min_total by adding MORE sources for the first differential item
    if len(picked) < min_total and diff:
        primary = diff[0]
        toks = _item_tokens(primary)
        # Build a strong outpatient query from the primary item
        q = (primary + " treatment management outpatient NSAID acetaminophen guideline").strip()
        try:
            chunks = retrieve(q, k=80) or []
        except TypeError:
            chunks = retrieve(q) or []
        chunks = [c for c in chunks if isinstance(c, dict)]
        chunks.sort(key=lambda c: _score_chunk_for_item(c, primary), reverse=True)

        for ch in chunks[:40]:
            if len(picked) >= min_total:
                break
            url = (ch.get("url") or ch.get("source") or "").strip()
            if not url or url in seen_urls:
                continue
            title = (ch.get("title") or "").strip()
            blob = (title + " " + url + " " + (ch.get("text") or "")).lower()
            # Require at least one strong token overlap for top-up items
            if toks and not any(t in blob for t in toks[:4]):
                continue
            seen_urls.add(url)
            picked.append(ch)

    refs = as_evidence_refs(picked) or []
    if refs:
        # enforce min_total in final output if available
        note.evidence = refs[:max_total]
    return note
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
    backup = FINALIZE.with_suffix(FINALIZE.suffix + f".bak_EVIDREBUILD_{ts}")
    backup.write_text(txt, encoding="utf-8")

    new_txt = txt[:s] + NEW_BLOCK.lstrip("\n") + "\n\n" + txt[e:]

    FINALIZE.write_text(new_txt, encoding="utf-8")
    print(f"OK: Patched {FINALIZE}")
    print(f"Backup: {backup}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
