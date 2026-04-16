#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime

NB = Path("app/clinical/note_builder.py")

HELPER = r'''
def _cc_normalize_evidence_refs_to_urls(evidence):
    """
    Convert EvidenceRef/dict items that reference local library files (content.txt, chunk_###.json, library/<hash>/...)
    into proper (title, url) citations using the nearest source.json.

    This fixes Evidence Used collapsing because downstream formatting drops non-http URLs.
    """
    if not isinstance(evidence, list) or not evidence:
        return evidence or []

    import json
    from pathlib import Path

    def _get_fields(ev):
        if isinstance(ev, dict):
            title = str(ev.get("title") or "").strip()
            url = str(ev.get("url") or ev.get("source") or "").strip()
            return title, url, ev
        title = str(getattr(ev, "title", "") or "").strip()
        url = str(getattr(ev, "url", "") or "").strip()
        if not url:
            url = str(getattr(ev, "source", "") or "").strip()
        return title, url, ev

    def _set_fields(ev_obj, title, url):
        if isinstance(ev_obj, dict):
            ev_obj = dict(ev_obj)
            ev_obj["title"] = title
            ev_obj["url"] = url
            ev_obj["source"] = url
            return ev_obj
        # EvidenceRef-like
        try:
            setattr(ev_obj, "title", title)
        except Exception:
            pass
        try:
            setattr(ev_obj, "url", url)
        except Exception:
            pass
        try:
            setattr(ev_obj, "source", url)
        except Exception:
            pass
        return ev_obj

    def _looks_local(u: str) -> bool:
        u = (u or "").strip()
        if not u:
            return True
        if u.startswith("http://") or u.startswith("https://"):
            return False
        # common local patterns in your debug
        if "library/" in u or u.endswith("content.txt") or u.endswith(".json"):
            return True
        return True

    def _find_source_json(path_str: str):
        if not path_str:
            return None
        p = Path(path_str)
        # sometimes url is like "library/<hash>/content.txt" (relative)
        if not p.is_absolute():
            # try relative to data/sources/
            # your debug shows "library/<hash>/content.txt" which likely lives under data/sources/library/
            p2 = Path("data/sources") / p
            if p2.exists():
                p = p2
        # walk up a few levels looking for source.json
        cur = p.parent
        for _ in range(6):
            sj = cur / "source.json"
            if sj.exists():
                return sj
            cur = cur.parent
        return None

    out = []
    seen = set()

    for ev in evidence:
        title, url, obj = _get_fields(ev)

        if _looks_local(url):
            sj = _find_source_json(url)
            if sj is not None:
                try:
                    meta = json.loads(sj.read_text(encoding="utf-8"))
                    real_url = str(meta.get("url") or "").strip()
                    real_title = str(meta.get("title") or "").strip()
                    if real_url.startswith(("http://", "https://")):
                        obj2 = _set_fields(obj, real_title or title or "Source", real_url)
                        title, url, obj = _get_fields(obj2)
                except Exception:
                    pass

        # final filter: keep only real URLs for Evidence Used
        if not url.startswith(("http://", "https://")):
            continue

        k = ("url:" + url).lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(obj)

    return out
'''

def main() -> int:
    if not NB.exists():
        print(f"ERROR: missing {NB}")
        return 2

    txt = NB.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = NB.with_suffix(NB.suffix + f".bak_NORMEVID_{ts}")
    bak.write_text(txt, encoding="utf-8")

    # Insert helper before build_note so it's defined
    if "def _cc_normalize_evidence_refs_to_urls(" not in txt:
        anchor = "def build_note(state: CallState) -> NoteDraft:"
        idx = txt.find(anchor)
        if idx == -1:
            print("ERROR: could not find build_note anchor")
            return 2
        txt = txt[:idx] + HELPER + "\n\n" + txt[idx:]

    # Add normalization call right before SOAP generation call
    # Your snippet shows: soap_text, err = _generate_llm_soap_with_retry(state, evidence)
    call_anchor = "soap_text, err = _generate_llm_soap_with_retry(state, evidence)"
    if call_anchor not in txt:
        print("ERROR: could not find SOAP generation anchor line")
        return 2

    if "evidence = _cc_normalize_evidence_refs_to_urls(evidence)" not in txt:
        txt = txt.replace(
            call_anchor,
            "evidence = _cc_normalize_evidence_refs_to_urls(evidence)\n\n    " + call_anchor,
            1
        )

    NB.write_text(txt, encoding="utf-8")
    print(f"OK: patched {NB}")
    print(f"Backup: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
