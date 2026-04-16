import json
from pathlib import Path
import re
import urllib.request
from urllib.error import HTTPError, URLError

MANIFEST_ROOT = Path("data/sources/pathways")

def clean_text(html: str) -> str:
    html = re.sub(r"(?s)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?s)<style.*?>.*?</style>", " ", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html).strip()
    return html

def fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "CallCareEvidenceBot/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return data.decode(errors="ignore")

def main():
    if not MANIFEST_ROOT.exists():
        raise SystemExit("No manifests found at data/sources/pathways")

    errors = []
    fetched = 0

    for manifest_path in MANIFEST_ROOT.glob("*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pid = manifest["pathway_id"]
        out_dir = manifest_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        for src in manifest.get("sources", []):
            sid = src.get("id") or src.get("source_id")
            url = src["url"]
            title = src.get("title", sid)

            print(f"Fetching {pid}:{sid}")

            try:
                raw = fetch_url(url)
                text = clean_text(raw)[:12000]  # keep it light

                doc = {
                    "pathway_id": pid,
                    "source_id": sid,
                    "title": title,
                    "url": url,
                    "text": text,
                }
                (out_dir / f"{sid}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
                fetched += 1

            except HTTPError as e:
                errors.append({"pathway_id": pid, "source_id": sid, "url": url, "error": f"HTTP {e.code}: {e.reason}"})
                print(f"  -> SKIP (HTTP {e.code}) {url}")

            except URLError as e:
                errors.append({"pathway_id": pid, "source_id": sid, "url": url, "error": f"URL error: {e.reason}"})
                print(f"  -> SKIP (URL error) {url}")

            except Exception as e:
                errors.append({"pathway_id": pid, "source_id": sid, "url": url, "error": str(e)})
                print(f"  -> SKIP (error) {url}")

    Path("logs").mkdir(exist_ok=True)
    Path("logs/fetch_errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")

    print(f"\nDone. Fetched: {fetched} documents.")
    if errors:
        print(f"Some sources failed ({len(errors)}). See logs/fetch_errors.json")

if __name__ == "__main__":
    main()
