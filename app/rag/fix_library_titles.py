#!/usr/bin/env python3
"""
Fix missing/garbage titles in data/sources/library/*/source.json by refetching page <title>.

This addresses Evidence Used lines like:
- source.json. Accessed .... https://...

We do NOT change any clinical logic.
We only repair metadata so citations render correctly.

Rules:
- If source.json has title missing/blank/"source.json"/"untitled", refetch URL and parse <title>.
- Then normalize titles into: "<Condition/Topic> - <Publisher>" when possible.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

LIB_DIR = Path("data/sources/library")
TIMEOUT = 25

# Simple domain -> publisher label (no diagnosis hacks)
PUBLISHER_BY_DOMAIN = {
    "www.nhs.uk": "NHS",
    "nhs.uk": "NHS",
    "www.nhsinform.scot": "NHS Inform",
    "nhsinform.scot": "NHS Inform",
    "www.mayoclinic.org": "Mayo Clinic",
    "mayoclinic.org": "Mayo Clinic",
    "www.aafp.org": "AAFP",
    "aafp.org": "AAFP",
    "www.ncbi.nlm.nih.gov": "NCBI Bookshelf",
    "ncbi.nlm.nih.gov": "NCBI Bookshelf",
    "now.aapmr.org": "AAPM&R KnowledgeNow",
    "aapmr.org": "AAPM&R KnowledgeNow",
}

BAD_TITLES = {"", "source.json", "content.txt", "unknown", "untitled", "none", "null"}


def get_domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().strip()
    except Exception:
        return ""


def fetch_html_title(url: str) -> str:
    headers = {"User-Agent": "CallCareTitleFix/1.0 (+https://example.invalid)"}
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def normalize_title(raw_title: str, url: str) -> str:
    """
    Try to convert noisy HTML titles into something that looks like:
    "Costochondritis - NHS"
    "Costochondritis - Mayo Clinic"
    "Costochondritis | AAFP" -> "Costochondritis - AAFP"
    """
    t = (raw_title or "").strip()
    if not t:
        return ""

    domain = get_domain(url)
    publisher = PUBLISHER_BY_DOMAIN.get(domain, "")

    # Common separators seen in HTML titles
    # We avoid regex; we just split on the most common characters.
    for sep in ["|", "–", "—"]:
        if sep in t:
            parts = [p.strip() for p in t.split(sep) if p.strip()]
            if parts:
                t = parts[0]
            break

    # Sometimes titles are like "Costochondritis - NHS"
    # If already contains a known publisher label, keep as-is (light cleanup only).
    low = t.lower()
    if publisher and publisher.lower() in low:
        # Ensure consistent "X - Publisher" format if possible
        if " - " in t:
            left = t.split(" - ")[0].strip()
            return f"{left} - {publisher}" if left else t
        return t

    # If we have a publisher label, append it.
    if publisher:
        return f"{t} - {publisher}"

    # Otherwise keep the cleaned first segment.
    return t


def main() -> int:
    if not LIB_DIR.exists():
        print(f"ERROR: library dir not found: {LIB_DIR}")
        return 2

    fixed = 0
    skipped = 0
    failed = 0

    dirs = [d for d in LIB_DIR.iterdir() if d.is_dir()]
    print(f"Library dirs: {len(dirs)}")

    for d in dirs:
        meta_path = d / "source.json"
        if not meta_path.exists():
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        url = str(meta.get("url", "")).strip()
        if not url.startswith("http"):
            skipped += 1
            continue

        title = str(meta.get("title", "")).strip()
        if title.lower() not in BAD_TITLES:
            skipped += 1
            continue

        print(f"FIX: {url}")
        try:
            raw = fetch_html_title(url)
            new_title = normalize_title(raw, url)
            if not new_title:
                print("  FAIL: empty extracted title")
                failed += 1
                continue
            meta["title"] = new_title
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  OK: {new_title}")
            fixed += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print("")
    print(f"DONE. fixed={fixed} skipped={skipped} failed={failed}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
