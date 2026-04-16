from __future__ import annotations

from datetime import date
from typing import Optional, List, Dict, Any, Callable, Tuple
from urllib.parse import urlparse, urlunparse
import re

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore


def _today_iso() -> str:
    try:
        return date.today().isoformat()
    except Exception:
        return ""


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _strip_tracking(url: str) -> str:
    try:
        p = urlparse(url)
        clean = p._replace(query="", fragment="")
        return urlunparse(clean)
    except Exception:
        return url


def _allow_hosts() -> Tuple[str, ...]:
    return (
        "nhs.uk",
        "nice.org.uk",
        "aafp.org",
        "cdc.gov",
        "medlineplus.gov",
        "nih.gov",
        "ncbi.nlm.nih.gov",
        "pubmed.ncbi.nlm.nih.gov",
        "who.int",
        "mayoclinic.org",
        "merckmanuals.com",
        "idsociety.org",
        "acog.org",
        "accessdata.fda.gov",
    )


def _is_allowed(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    h = _host(u)
    if not h:
        return False
    for ah in _allow_hosts():
        if h == ah or h.endswith("." + ah):
            return True
    return False


def _extract_url_citations(resp: Any) -> List[Dict[str, Any]]:
    out = getattr(resp, "output", None)
    if out is None and isinstance(resp, dict):
        out = resp.get("output")
    if not isinstance(out, list):
        return []

    results: List[Dict[str, Any]] = []

    for item in out:
        t = getattr(item, "type", None) if not isinstance(item, dict) else item.get("type")
        if t != "message":
            continue

        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            annotations = part.get("annotations") if isinstance(part, dict) else getattr(part, "annotations", None)
            if not isinstance(annotations, list):
                continue

            for a in annotations:
                if isinstance(a, dict):
                    atype = a.get("type")
                    title = a.get("title")
                    url = a.get("url")
                else:
                    atype = getattr(a, "type", None)
                    title = getattr(a, "title", None)
                    url = getattr(a, "url", None)

                if atype != "url_citation":
                    continue
                if not title or not url:
                    continue

                url = _strip_tracking(str(url).strip())
                if not _is_allowed(url):
                    continue

                title = re.sub(r"\s+", " ", str(title)).strip()
                if not title:
                    continue

                results.append(
                    {
                        "title": title,
                        "source": _host(url) or "source",
                        "url": url,
                        "accessed": _today_iso(),
                        "snippet": "",
                    }
                )

    seen = set()
    final: List[Dict[str, Any]] = []
    for r in results:
        u = (r.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        final.append(r)
    return final


def _publisher_prefixes() -> List[str]:
    """
    Publisher-name fallbacks to coax web_search toward allowed domains
    WITHOUT using site: operators (your query LLM forbids them).
    """
    return [
        "NHS",
        "NICE",
        "AAFP",
        "CDC",
        "MedlinePlus",
        "Mayo Clinic",
        "Merck Manual",
        "IDSA",
        "ACOG",
        "FDA",
        "WHO",
        "PubMed",
        "NCBI",
    ]


def _dedupe_by_url(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in items:
        u = (r.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(r)
    return out


def llm_http_evidence(
    *,
    chief_complaint: str,
    queries: Optional[List[str]] = None,
    diagnosis_hint: Optional[str] = None,
    context: Optional[str] = None,
    assessment_text: Optional[str] = None,
    min_urls: int = 4,
    max_urls: int = 6,
    timeout_s: int = 12,
    max_queries: int = 4,
    debug: bool = False,
    trace_fn: Optional[Callable[..., None]] = None,
    **_ignored,
) -> List[Dict[str, Any]]:
    """
    WEB ONLY. STRICT url_citation extraction.
    NO PREVIEW. NO AUTO-ACQUIRE. NO HTML fetch. NO guessed titles.

    Key behavior:
    - Executes up to max_queries queries (prefer query_llm output).
    - For each query: run as-is.
      If it yields 0 allowed citations, retry with publisher-name prefixes
      (bounded attempts) to steer results onto allowed sources.
    """
    if OpenAI is None:
        raise RuntimeError("openai SDK not available")

    cc = (chief_complaint or "").strip()
    anchor = (
        (assessment_text or "").strip()
        or (diagnosis_hint or "").strip()
        or (context or "").strip()
        or cc
    ).strip()
    if not anchor:
        return []

    qs: List[str] = []
    if isinstance(queries, list) and queries:
        qs = [str(x).strip() for x in queries if str(x).strip()]
    if not qs:
        qs = [
            f"{anchor} outpatient management guideline",
            f"{anchor} medication dosing contraindications adult",
            f"{anchor} red flags imaging testing indications",
        ]
    qs = qs[: max(1, int(max_queries or 1))]

    client = OpenAI()
    model = "gpt-4.1-mini"

    collected: List[Dict[str, Any]] = []
    prefixes = _publisher_prefixes()

    # Bound retries to control latency:
    # per query: 1 base attempt + up to 3 publisher-prefixed attempts
    max_prefix_attempts_per_query = 3

    for q in qs:
        attempts: List[str] = [q]

        # Only add publisher attempts if the base attempt yields 0 allowed citations
        # (we decide after the first call).
        try:
            resp = client.responses.create(
                model=model,
                input=attempts[0],
                tools=[{"type": "web_search"}],
                timeout=timeout_s,
            )
            items = _extract_url_citations(resp)
        except Exception as e:
            items = []
            if trace_fn:
                try:
                    trace_fn(marker="web_search_error", error=str(e), query=attempts[0])
                except Exception:
                    pass

        if items:
            collected.extend(items)
        else:
            # Try a few publisher-prefixed queries (bounded)
            for pref in prefixes[:max_prefix_attempts_per_query]:
                q2 = f"{pref} {q}"
                try:
                    resp2 = client.responses.create(
                        model=model,
                        input=q2,
                        tools=[{"type": "web_search"}],
                        timeout=timeout_s,
                    )
                    items2 = _extract_url_citations(resp2)
                except Exception as e:
                    items2 = []
                    if trace_fn:
                        try:
                            trace_fn(marker="web_search_error", error=str(e), query=q2)
                        except Exception:
                            pass
                if items2:
                    collected.extend(items2)
                    break  # stop prefix retries once we got something

        collected = _dedupe_by_url(collected)

        if len(collected) >= int(min_urls or 0):
            break

    final = _dedupe_by_url(collected)[: int(max_urls or 6)]
    final = [r for r in final if isinstance(r, dict) and (r.get("url") or "").startswith("http")]

    if debug:
        print("EVID_QUERIES_USED =", qs)
        print("STRICT_EVID_LEN =", len(final))
        for i, r in enumerate(final[:6], 1):
            print(i, r.get("title"), "|", r.get("url"))

    return final
