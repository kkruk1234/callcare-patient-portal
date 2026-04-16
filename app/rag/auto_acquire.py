from __future__ import annotations

from typing import Any, Dict, List, Optional


def acquire_ingest_reindex(
    *,
    query: str,
    max_urls: int = 8,
    force: bool = False,
    debug: bool = False,
    **_kwargs: Any,
) -> Dict[str, Any]:
    """
    WEB-ONLY MODE: disabled.

    Previously: web search + ingest + reindex local evidence library.
    Now: no-op return.
    """
    return {"urls": [], "allowed_urls": [], "ingested": 0, "reindexed": False}


def acquire_ingest(
    *,
    query: str,
    max_urls: int = 8,
    force: bool = False,
    debug: bool = False,
    **_kwargs: Any,
) -> Dict[str, Any]:
    """
    WEB-ONLY MODE: disabled.
    """
    return {"urls": [], "allowed_urls": [], "ingested": 0}


def _openai_web_search_urls(
    query: str,
    *,
    max_urls: int = 8,
    timeout_s: int = 12,
    debug: bool = False,
    **_kwargs: Any,
) -> List[str]:
    """
    Legacy helper referenced by some callers. Disabled in web-only mode.
    """
    return []
