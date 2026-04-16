#!/usr/bin/env python3
"""
Fix docs.json titles using the ingested library metadata.

Problem this fixes:
- Evidence Used lines show "source.json" because docs.json titles are missing,
  so downstream formatting falls back to a filename.

What this does:
- Loads data/index/docs.json
- For each doc, tries to match it to a library source.json by id or url
- Sets doc["title"] from library meta["title"] when missing/garbage
- If meta title missing, generates a reasonable title from URL path
- Writes data/index/docs.json back (backup created)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

DOCS_PATH = Path("data/index/docs.json")
LIB_DIR = Path("data/sources/library")

def url_fallback_title(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.netloc or "").strip()
        path = (p.path or "").strip().strip("/")
        if not host:
            return ""
        if not path:
            return host
        # last path segment
        seg = path.split("/")[-1]
        seg = seg.replace("-", " ").replace("_", " ").strip()
        if seg.lower().endswith(".pdf"):
            seg = seg[:-4].strip()
        # Title-case-ish without regex
        words = [w for w in seg.split(" ") if w]
        seg_pretty = " ".join([w[:1].upper() + w[1:] if w else w for w in words])
        return f"{seg_pretty} - {host}"
    except Exception:
        return ""

def load_library_meta():
    by_id = {}
    by_url = {}
    if not LIB_DIR.exists():
        return by_id, by_url
    for d in LIB_DIR.iterdir():
        if not d.is_dir():
            continue
        meta_path = d / "source.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = str(meta.get("id", "")).strip() or d.name
        url = str(meta.get("url", "")).strip()
        by_id[sid] = meta
        if url:
            by_url[url] = meta
    return by_id, by_url

def main() -> int:
    if not DOCS_PATH.exists():
        print(f"ERROR: missing {DOCS_PATH}")
        return 2

    docs = json.loads(DOCS_PATH.read_text(encoding="utf-8"))
    if not isinstance(docs, list):
        print("ERROR: docs.json is not a list")
        return 2

    by_id, by_url = load_library_meta()

    changed = 0
    kept = 0

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("id", "")).strip()
        url = str(doc.get("url", "")).strip()
        title = str(doc.get("title", "")).strip()

        bad_title = (not title) or (title.lower() in ["source.json", "content.txt", "unknown", "untitled"])

        if not bad_title:
            kept += 1
            continue

        meta = None
        if doc_id and doc_id in by_id:
            meta = by_id[doc_id]
        elif url and url in by_url:
            meta = by_url[url]

        new_title = ""
        if meta:
            new_title = str(meta.get("title", "")).strip()

        if not new_title and url:
            new_title = url_fallback_title(url)

        if new_title:
            doc["title"] = new_title
            changed += 1
        else:
            kept += 1

    # backup then write
    backup = DOCS_PATH.with_suffix(".json.bak_titles_fix")
    if not backup.exists():
        backup.write_text(DOCS_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    DOCS_PATH.write_text(json.dumps(docs, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"OK. Updated titles for {changed} docs. Backup at {backup}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
