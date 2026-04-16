#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

NB = Path("app/clinical/note_builder.py")

INSERT_AFTER = "evidence = _cc_topup_evidence_titlematch(state, evidence, min_title_hits=2, max_total=5)"
INSERT_LINE = "    evidence = _cc_guard_evidence_min3_and_relevance(state, evidence, min_total=3, max_total=5)\n"

HELPER = r'''

def _cc_guard_evidence_min3_and_relevance(state, evidence, min_total=3, max_total=5):
    """
    Final evidence guardrail (generalizable; no hard-coded diagnoses):
      - Build tokens from chief complaint + pathway_id
      - Prefer evidence whose title/url matches tokens
      - If we already have >= min_total token-matching items, drop non-matching items (e.g., rule-out noise)
      - Ensure >= min_total by topping up from RAG using a constrained query built from the same tokens
      - If complaint suggests pain (or pathway contains pain-like tokens), force-add AAFP acute pain dosing anchor from docs.json

    This prevents "Pericarditis - NHS" from surviving in benign MSK cases when better matches exist.
    """

    if evidence is None:
        evidence = []

    # --- token set from CC + pathway_id (no diagnosis lists) ---
    cc = str(getattr(state, "chief_complaint", "") or "").lower().strip()
    pid = str(getattr(state, "pathway_id", "") or "").lower().replace("_", " ").strip()
    blob = (cc + " " + pid).strip()

    # keep meaningful tokens only
    toks = []
    for w in blob.replace("/", " ").replace("-", " ").split():
        w = w.strip()
        if len(w) >= 5 and w not in ("possible", "likely", "unlikely", "about", "today", "cause", "causes"):
            toks.append(w)
    toks = toks[:10]

    def ev_title_url(ev):
        if isinstance(ev, dict):
            t = str(ev.get("title") or "").strip()
            u = str(ev.get("url") or ev.get("source") or "").strip()
            return t, u
        t = str(getattr(ev, "title", "") or "").strip()
        u = str(getattr(ev, "url", "") or "").strip()
        if not u:
            u = str(getattr(ev, "source", "") or "").strip()
        return t, u

    def matches(ev):
        if not toks:
            return False
        t, u = ev_title_url(ev)
        b = (t + " " + u).lower()
        return any(x in b for x in toks[:6])

    # 1) Sort evidence so token matches come first
    ranked = list(evidence)
    ranked.sort(key=lambda ev: (1 if matches(ev) else 0), reverse=True)

    # 2) If we have enough matching items, drop non-matching ones
    matching = [ev for ev in ranked if matches(ev)]
    if len(matching) >= min_total:
        ranked = matching

    # Trim to max_total for now
    ranked = ranked[:max_total]

    # 3) Ensure >= min_total by topping up from RAG (but only add items that match tokens)
    if len(ranked) < min_total and toks:
        try:
            from app.rag.retrieve import retrieve
        except Exception:
            return ranked[:max_total]

        q = (" ".join(toks[:4]) + " treatment management guideline outpatient").strip()
        try:
            chunks = retrieve(q, k=80) or []
        except TypeError:
            chunks = retrieve(q) or []

        # Keep distinct URLs
        seen = set()
        for ev in ranked:
            _, u = ev_title_url(ev)
            if u:
                seen.add(u)

        for ch in chunks:
            if len(ranked) >= min_total:
                break
            if not isinstance(ch, dict):
                continue
            url = str(ch.get("url") or ch.get("source") or "").strip()
            if not url or url in seen:
                continue
            title = str(ch.get("title") or "").strip()
            b = (title + " " + url).lower()
            if not any(x in b for x in toks[:6]):
                continue
            ch = dict(ch)
            ch["key"] = ch.get("key") or f"EVID_GUARD_{len(ranked)+1}"
            ranked.append(ch)
            seen.add(url)

        ranked = ranked[:max_total]

    # 4) Dosing anchor: if complaint/pathway suggests pain, force-add AAFP acute pain page from docs.json
    wants_pain_anchor = ("pain" in cc) or ("pain" in pid) or ("costochond" in pid) or ("chest" in cc and "pain" in cc)
    if wants_pain_anchor:
        have_anchor = False
        for ev in ranked:
            t, u = ev_title_url(ev)
            b = (t + " " + u).lower()
            if "/p63.html" in b or "pharmacologic therapy for acute pain" in b:
                have_anchor = True
                break

        if not have_anchor:
            try:
                import json
                docs = json.load(open("data/index/docs.json", "r", encoding="utf-8"))
            except Exception:
                docs = []

            cand = None
            for d in docs if isinstance(docs, list) else []:
                if not isinstance(d, dict):
                    continue
                url = str(d.get("url") or "").lower()
                title = str(d.get("title") or "").lower()
                if "/p63.html" in url or "pharmacologic therapy for acute pain" in title:
                    cand = d
                    break

            if cand and len(ranked) < max_total:
                # Add as dict chunk; your pipeline already tolerates dict evidence
                ranked.append({"title": cand.get("title", ""), "url": cand.get("url", ""), "source": cand.get("url", ""), "key": f"EVID_DOSING_{len(ranked)+1}"})

    return ranked[:max_total]
'''

def main() -> int:
    if not NB.exists():
        print(f"ERROR: missing {NB}")
        return 2

    txt = NB.read_text(encoding="utf-8")

    # 1) Insert call into build_note once
    if "_cc_guard_evidence_min3_and_relevance" in txt and "evidence = _cc_guard_evidence_min3_and_relevance" in txt:
        print("OK: guard already present; no changes applied.")
        return 0

    if INSERT_AFTER not in txt:
        print("ERROR: could not find insertion anchor line in note_builder.py")
        print("Anchor expected:", INSERT_AFTER)
        return 2

    # Insert guard call right after the second titlematch topup line (we anchor on the exact string)
    txt2 = txt.replace(INSERT_AFTER + "\n", INSERT_AFTER + "\n" + INSERT_LINE, 1)

    # 2) Append helper near the end (before EOF)
    if "_cc_guard_evidence_min3_and_relevance" not in txt2:
        txt2 = txt2 + "\n" + HELPER + "\n"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = NB.with_suffix(NB.suffix + f".bak_EVIDGUARD_{ts}")
    backup.write_text(txt, encoding="utf-8")

    NB.write_text(txt2, encoding="utf-8")
    print(f"OK: Patched {NB}")
    print(f"Backup: {backup}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
