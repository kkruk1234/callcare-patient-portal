import os
import re
import json
import time
import hashlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple, Optional
from urllib.parse import urlparse, urljoin, urldefrag

import requests

ROOT = Path("data/sources/library")
SEED_DIR = Path("data/sources/library_seed")
HUBS_TXT = SEED_DIR / "hub_urls.txt"
DOMAINS_TXT = SEED_DIR / "allowlist_domains.txt"

USER_AGENT = os.getenv(
    "CALLCARE_LIBRARY_USER_AGENT",
    "CallCareEvidenceBot/1.0 (+offline-indexing; allowlisted-sources-only)",
)

TIMEOUT_SECS = float(os.getenv("CALLCARE_LIBRARY_TIMEOUT_SECS", "20"))
CHUNK_CHARS = int(os.getenv("CALLCARE_LIBRARY_CHUNK_CHARS", "1400"))
CHUNK_OVERLAP = int(os.getenv("CALLCARE_LIBRARY_CHUNK_OVERLAP", "150"))
MAX_CHUNKS_PER_URL = int(os.getenv("CALLCARE_LIBRARY_MAX_CHUNKS_PER_URL", "80"))
OVERWRITE = os.getenv("CALLCARE_LIBRARY_OVERWRITE", "0") == "1"

MAX_PAGES = int(os.getenv("CALLCARE_LIBRARY_MAX_PAGES", "800"))     # “full” but bounded
MAX_DEPTH = int(os.getenv("CALLCARE_LIBRARY_MAX_DEPTH", "3"))       # hop depth from hubs
SAME_DOMAIN_ONLY = os.getenv("CALLCARE_LIBRARY_SAME_DOMAIN_ONLY", "1") == "1"

# Content URL filters (keep medical/guideline-ish pages, skip junk)
SKIP_PATTERNS = [
    r"/privacy", r"/cookies", r"/accessibility", r"/sitemap", r"/search", r"/login",
    r"/subscribe", r"/account", r"/news", r"/press", r"/careers", r"/contact",
    r"/about", r"/foia", r"/spanish", r"/espanol", r"\.jpg$", r"\.png$", r"\.gif$",
    r"\.svg$", r"\.zip$", r"\.mp4$", r"\.mp3$", r"\.css$", r"\.js$",
]
KEEP_HINTS = [
    # Broad hints across sites:
    "conditions", "diseases", "guidance", "treatment", "recommend", "topic",
    "health", "symptoms", "diagnosis", "antibiotic", "sti", "vaccine", "hpv",
    "warts", "cystitis", "uti", "urinary", "respiratory",
]

def _read_lines(p: Path) -> List[str]:
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        out.append(ln)
    return out

def _norm_domain(d: str) -> str:
    d = (d or "").strip().lower()
    return d.lstrip(".")

def _url_domain(u: str) -> str:
    try:
        return _norm_domain(urlparse(u).netloc)
    except Exception:
        return ""

def _is_allowlisted(url: str, allow_domains: List[str]) -> bool:
    d = _url_domain(url)
    if not d:
        return False
    for ad in allow_domains:
        ad = _norm_domain(ad)
        if d == ad or d.endswith("." + ad):
            return True
    return False

def _publisher(dom: str) -> str:
    dom = _norm_domain(dom)
    parts = dom.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return dom or "unknown"

def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:90] if s else "doc"

def _hash(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:12]

def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</p\s*>", "\n", html)
    text = re.sub(r"(?is)<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _extract_title(html: str, url: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    if m:
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        if t:
            return t[:180]
    return url

def _chunk_text(text: str, chunk_chars: int, overlap: int, max_chunks: int) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    step = max(1, chunk_chars - max(0, overlap))
    chunks = []
    i = 0
    while i < len(t) and len(chunks) < max_chunks:
        ch = t[i : i + chunk_chars].strip()
        if ch:
            chunks.append(ch)
        i += step
    return chunks

def _skip_url(url: str) -> bool:
    u = (url or "").lower()
    for pat in SKIP_PATTERNS:
        if re.search(pat, u):
            return True
    return False

def _keep_url(url: str) -> bool:
    # Keep if it looks like a content page; don’t over-filter.
    u = (url or "").lower()
    return any(h in u for h in KEEP_HINTS) or ("/conditions/" in u) or ("/guidance/" in u)

def _safe_get(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECS)
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()
        # Only HTML for now
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            return None, f"skip non-html content-type: {ctype}"
        return resp.text, None
    except Exception as e:
        return None, str(e)

def _extract_links(html: str, base_url: str) -> List[str]:
    links = []
    # crude href extraction
    for m in re.finditer(r'(?is)\shref\s*=\s*["\']([^"\']+)["\']', html or ""):
        href = (m.group(1) or "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        absu = urljoin(base_url, href)
        absu, _frag = urldefrag(absu)
        links.append(absu)
    return links

def _out_dir_for(url: str) -> Path:
    dom = _url_domain(url)
    pub = _publisher(dom)
    uhash = _hash(url)
    name = _slug(urlparse(url).path) or "page"
    sid = f"{_slug(pub)}_{name}_{uhash}"
    return ROOT / _slug(pub) / sid

def _already_ingested(out_dir: Path) -> bool:
    return (out_dir / "chunk_001.json").exists()

def _write_chunks(url: str, title: str, publisher: str, text: str) -> int:
    out_dir = _out_dir_for(url)
    out_dir.mkdir(parents=True, exist_ok=True)

    if _already_ingested(out_dir) and not OVERWRITE:
        return -1  # already

    chunks = _chunk_text(text, CHUNK_CHARS, CHUNK_OVERLAP, MAX_CHUNKS_PER_URL)
    if not chunks:
        return 0

    date = time.strftime("%Y-%m-%d")
    for i, ch in enumerate(chunks, start=1):
        doc = {
            "title": title,
            "url": url,
            "publisher": publisher,
            "date": date,
            "text": ch,
            "source_type": "library",
            "source_id": out_dir.name,
            "chunk_id": f"chunk_{i:03d}",
        }
        (out_dir / f"chunk_{i:03d}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(chunks)

def build_library_by_crawl() -> None:
    allow_domains = _read_lines(DOMAINS_TXT)
    hubs = _read_lines(HUBS_TXT)
    if not hubs:
        raise RuntimeError(f"No hub URLs in {HUBS_TXT}")

    ROOT.mkdir(parents=True, exist_ok=True)

    q = deque()
    seen: Set[str] = set()

    def push(url: str, depth: int, hub_dom: str):
        if not url or url in seen:
            return
        if _skip_url(url):
            return
        if not _is_allowlisted(url, allow_domains):
            return
        if SAME_DOMAIN_ONLY and _url_domain(url) != hub_dom:
            return
        seen.add(url)
        q.append((url, depth, hub_dom))

    for h in hubs:
        if not _is_allowlisted(h, allow_domains):
            print("SKIP hub (not allowlisted):", h)
            continue
        dom = _url_domain(h)
        push(h, 0, dom)

    ok_pages = 0
    skipped = 0
    failed = 0
    kept_for_index = 0

    while q and ok_pages < MAX_PAGES:
        url, depth, hub_dom = q.popleft()

        html, err = _safe_get(url)
        if html is None:
            failed += 1
            continue

        title = _extract_title(html, url)
        publisher = _publisher(_url_domain(url))
        text = _strip_html(html)

        # Only ingest pages with enough text + that look content-like OR are hubs
        is_hub = (depth == 0)
        looks_content = _keep_url(url) or (len(text) > 2500)
        if len(text) < 500 or (not is_hub and not looks_content):
            skipped += 1
        else:
            n = _write_chunks(url, title, publisher, text)
            if n == -1:
                skipped += 1
            elif n == 0:
                failed += 1
            else:
                ok_pages += 1
                kept_for_index += 1
                print(f"OK [{ok_pages}/{MAX_PAGES}] depth={depth} {url} -> {n} chunks")

        # Expand links within depth bound
        if depth < MAX_DEPTH:
            for link in _extract_links(html, url):
                push(link, depth + 1, hub_dom)

    print("\n=== Crawl build complete ===")
    print("Ingested pages:", kept_for_index)
    print("Skipped:", skipped)
    print("Failed fetch/parse:", failed)
    print("Seen URLs:", len(seen))
    print("Output root:", ROOT)

if __name__ == "__main__":
    build_library_by_crawl()
