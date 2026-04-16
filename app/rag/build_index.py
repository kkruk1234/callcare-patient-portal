import os
import json
import numpy as np
import faiss

from app.clinical.embeddings import get_embedder  # EMBED_HARDEN_PATCH_V1

SRC_DIR = "data/sources"
OUT_DIR = "data/index"


def chunk_text(text: str, chunk_size=900, overlap=150):
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + chunk_size])
        i += chunk_size - overlap
        if i < 0:
            break
    return chunks


def _safe_read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        try:
            with open(path, "r", encoding="latin-1") as f:
                return f.read()
        except Exception:
            return ""


def _extract_text_from_json(obj) -> str:
    """
    Robustly extract the best 'text-like' field from a JSON blob.
    Your fetched docs usually include fields like: text, snippet, content, title, url.
    """
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        parts = []
        for item in obj:
            t = _extract_text_from_json(item)
            if t:
                parts.append(t)
        return "\n".join(parts).strip()

    if isinstance(obj, dict):
        # Prefer these fields if present
        for key in ("text", "content", "snippet", "summary", "abstract"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

        # Sometimes "extracted" text is nested
        for key in ("document", "page", "payload", "data"):
            v = obj.get(key)
            if isinstance(v, (dict, list)):
                t = _extract_text_from_json(v)
                if t:
                    return t

        # Fallback: stitch together string fields (avoid huge blobs)
        parts = []
        for k, v in obj.items():
            if isinstance(v, str) and v.strip():
                if k.lower() in ("url", "source", "id", "name"):
                    continue
                parts.append(v.strip())
        return "\n".join(parts).strip()

    return ""


def _extract_meta_from_json(obj) -> dict:
    meta = {}
    if isinstance(obj, dict):
        for key in ("url", "source", "title", "id"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                meta[key] = v.strip()
    return meta


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    model = get_embedder()  # EMBED_HARDEN_PATCH_V1

    docs = []
    files_seen = 0
    empty_skipped = 0

    for root, dirs, files in os.walk(SRC_DIR):
        # Ignore PubMed archive entirely (you explicitly moved it there)
        if "/_pubmed_archive" in root or root.endswith("_pubmed_archive"):
            continue

        for fn in files:
            path = os.path.join(root, fn)

            if fn.lower().endswith(".txt"):
                files_seen += 1
                txt = _safe_read_text(path)
                if not txt.strip():
                    empty_skipped += 1
                    continue
                rel = os.path.relpath(path, SRC_DIR)
                for c in chunk_text(txt):
                    docs.append(
                        {
                            "source": rel,
                            "url": "",
                            "title": fn,
                            "text": c,
                        }
                    )
                continue

            if fn.lower().endswith(".json"):
                files_seen += 1
                raw = _safe_read_text(path)
                if not raw.strip():
                    empty_skipped += 1
                    continue

                try:
                    obj = json.loads(raw)
                except Exception:
                    empty_skipped += 1
                    continue

                # Skip manifest.json (it’s references metadata, not evidence text)
                if fn.lower() == "manifest.json":
                    continue

                txt = _extract_text_from_json(obj)
                if not txt.strip():
                    empty_skipped += 1
                    continue

                meta = _extract_meta_from_json(obj)
                rel = os.path.relpath(path, SRC_DIR)

                url = meta.get("url", "") or meta.get("source", "")
                title = meta.get("title", "") or fn

                for c in chunk_text(txt):
                    docs.append(
                        {
                            "source": rel,
                            "url": url,
                            "title": title,
                            "text": c,
                        }
                    )

    if not docs:
        raise RuntimeError(
            f"No documents found to index under {SRC_DIR}. "
            f"files_seen={files_seen} empty_skipped={empty_skipped}"
        )

    embeddings = model.encode([d["text"] for d in docs], show_progress_bar=True)
    emb = np.array(embeddings).astype("float32")
    faiss.normalize_L2(emb)

    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)

    faiss.write_index(index, os.path.join(OUT_DIR, "faiss.index"))
    with open(os.path.join(OUT_DIR, "docs.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f)

    print(f"Indexed {len(docs)} chunks from {len(set(d['source'] for d in docs))} sources under {SRC_DIR} into {OUT_DIR}.")
    print(f"Files seen: {files_seen} | Empty skipped: {empty_skipped}")


if __name__ == "__main__":
    main()
