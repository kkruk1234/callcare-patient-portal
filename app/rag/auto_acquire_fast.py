from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class AcquireAttachResult:
    """
    Compatibility shim for the previous auto-acquire pipeline.

    In WEB-ONLY mode we do NOT auto-acquire, ingest, or attach anything.
    note_builder expects an object with .ingested_docs (list).
    """
    ingested_docs: List[Dict[str, Any]]


def acquire_ingest_attach(
    *,
    query: str,
    max_urls: int = 5,
    time_budget_sec: float = 10.0,
    force: bool = False,
    debug: bool = False,
    **_kwargs: Any,
) -> AcquireAttachResult:
    """
    WEB-ONLY MODE: disabled.

    Previously: web search + ingest + attach to local library.
    Now: return nothing, quickly, deterministically.
    """
    return AcquireAttachResult(ingested_docs=[])


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
