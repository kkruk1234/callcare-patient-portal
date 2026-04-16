"""
Embeddings loader for CallCare RAG.

Critical requirement:
- get_embedder() must NEVER silently return None.
- If dependencies are missing, raise a clear error message.
"""

from __future__ import annotations

from typing import Optional

_MODEL = None


def get_embedder(model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
    """
    Returns a SentenceTransformer embedder used to build/query the FAISS index.

    This project’s FAISS index in data/index/ was built using the MiniLM model
    embeddings, so retrieval MUST use the same embedding family.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "RAG embeddings are not available because 'sentence-transformers' is not installed.\n\n"
            "Fix:\n"
            "  python3 -m pip install -U sentence-transformers\n\n"
            f"Original import error: {type(e).__name__}: {e}"
        )

    try:
        _MODEL = SentenceTransformer(model_name)
    except Exception as e:
        raise RuntimeError(
            "Failed to initialize the SentenceTransformer embedding model.\n\n"
            f"Model name: {model_name}\n"
            "This usually means:\n"
            "- model download failed (no internet / blocked), or\n"
            "- PyTorch is missing/broken.\n\n"
            "Try:\n"
            "  python3 -m pip install -U torch sentence-transformers\n\n"
            f"Original init error: {type(e).__name__}: {e}"
        )

    return _MODEL
