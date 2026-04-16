from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import html as _html

try:
    from app.core.models import ReviewPacket, NoteDraft  # type: ignore
except Exception:
    ReviewPacket = None  # type: ignore
    NoteDraft = None  # type: ignore

FINAL_DIR = Path("logs") / "finalized"
FINAL_DIR.mkdir(parents=True, exist_ok=True)

CALLCARE_FINALIZE = "WEBONLY_FINALIZE_V9J"
FINALIZE_SIGNATURE = "(session_id: Any, note: Any, state: Any) -> Any"


def now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _safe_str(x: Any) -> str:
    try:
        return str(x if x is not None else "").strip()
    except Exception:
        return ""


def _host_from_url(url: str) -> str:
    u = _safe_str(url).lower()
    if not u:
        return ""
    try:
        host = re.sub(r"^https?://", "", u).split("/")[0]
        return host.replace("www.", "").strip()
    except Exception:
        return ""


def _domain_group_from_url(url: str) -> str:
    host = _host_from_url(url)
    if not host:
        return "Source"
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _answers_dict(state: Any) -> Dict[str, Any]:
    try:
        sx = getattr(state, "symptoms", None) or {}
        if isinstance(sx, dict):
            a = sx.get("_answers", {})
            return a if isinstance(a, dict) else {}
    except Exception:
        pass
    return {}


def _chief_complaint(state: Any) -> str:
    return _safe_str(getattr(state, "chief_complaint", None) or getattr(state, "chief", None) or "")


def _get_queries_from_state(state: Any) -> List[str]:
    qs: List[str] = []
    ans = _answers_dict(state)

    v = ans.get("evidence_search_queries")
    if isinstance(v, list):
        qs = [str(x).strip() for x in v if str(x).strip()]

    if not qs:
        try:
            v2 = getattr(state, "evidence_search_queries", None)
            if isinstance(v2, list):
                qs = [str(x).strip() for x in v2 if str(x).strip()]
        except Exception:
            pass

    out: List[str] = []
    for q in qs:
        q = re.sub(r"\s+", " ", q).strip()
        if not q:
            continue
        if q not in out:
            out.append(q)
        if len(out) >= 4:
            break
    return out


def _run_query_llm_if_available(state: Any) -> List[str]:
    try:
        from app.clinical import query_llm  # type: ignore
    except Exception:
        return []

    for fn_name in ("generate_evidence_queries", "generate_queries", "build_queries"):
        fn = getattr(query_llm, fn_name, None)
        if callable(fn):
            try:
                res = fn(state)
                if isinstance(res, list):
                    out: List[str] = []
                    for x in res:
                        q = re.sub(r"\s+", " ", str(x)).strip()
                        if q and q not in out:
                            out.append(q)
                        if len(out) >= 4:
                            break
                    return out
            except Exception:
                continue
    return []


def _generic_fallback_queries(state: Any) -> List[str]:
    cc = _chief_complaint(state).strip()
    dx = _safe_str(getattr(state, "working_diagnosis", None) or getattr(state, "diagnosis", None) or "").strip()
    base = re.sub(r"\s+", " ", (dx or cc)).strip()
    return [base[:80] or "clinical guidance"]


def _expand_queries_by_publisher(base_queries: List[str], max_total: int = 12) -> List[str]:
    publishers = [
        "AAFP",
        "MedlinePlus",
        "CDC",
        "NIH",
        "NICE",
        "WHO",
        "Mayo Clinic",
        "Cleveland Clinic",
        "NHS",
    ]
    base = [q for q in base_queries if q.strip()]
    if not base:
        return []
    base = base[:2]

    expanded: List[str] = []
    for p in publishers:
        for q in base:
            expanded.append(f"{p} {q}".strip())
            if len(expanded) >= max_total:
                return expanded
    return expanded[:max_total]


def _call_llm_http_evidence(state: Any, queries: List[str], max_urls: int, max_per_publisher: int) -> List[Dict[str, Any]]:
    from app.rag.llm_http_evidence import llm_http_evidence  # type: ignore

    cc = _chief_complaint(state) or "(not captured)"

    last_err: Exception | None = None
    for call_style in ("kw_cc", "kw_cc_alt", "minimal"):
        try:
            if call_style == "kw_cc":
                items = llm_http_evidence(
                    queries=queries,
                    chief_complaint=cc,
                    max_urls=max_urls,
                    max_per_publisher=max_per_publisher,
                    no_preview=True,
                )
            elif call_style == "kw_cc_alt":
                items = llm_http_evidence(
                    queries=queries,
                    cc=cc,
                    max_urls=max_urls,
                    max_per_publisher=max_per_publisher,
                    no_preview=True,
                )
            else:
                items = llm_http_evidence(
                    queries=queries,
                    chief_complaint=cc,
                    max_urls=max_urls,
                    max_per_publisher=max_per_publisher,
                )

            out: List[Dict[str, Any]] = []
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        out.append(dict(it))
                    else:
                        out.append(
                            {
                                "title": _safe_str(getattr(it, "title", "")),
                                "source": _safe_str(getattr(it, "source", "") or getattr(it, "publisher", "")),
                                "url": _safe_str(getattr(it, "url", "")),
                                "accessed": _safe_str(getattr(it, "accessed", "") or getattr(it, "date", "")),
                                "snippet": _safe_str(getattr(it, "snippet", "") or getattr(it, "text", "")),
                            }
                        )
            return out
        except Exception as e:
            last_err = e
            continue

    raise last_err if last_err else RuntimeError("llm_http_evidence call failed")


def _topic_terms(state: Any) -> List[str]:
    text = " ".join(
        [
            _chief_complaint(state),
            _safe_str(getattr(state, "working_diagnosis", None) or ""),
            _safe_str(getattr(state, "diagnosis", None) or ""),
            _safe_str(getattr(state, "pathway_id", None) or "").replace("_", " "),
        ]
    ).lower()

    words = re.findall(r"[a-z][a-z\-]{2,}", text)
    stop = {
        "with", "without", "from", "that", "this", "these", "those", "pain", "adult",
        "child", "acute", "chronic", "care", "clinical", "guidance", "disease",
        "infection", "visit", "phone", "only", "possible", "unknown", "general",
        "medical", "health", "management", "symptoms",
    }

    out: List[str] = []
    for w in words:
        if w in stop:
            continue
        if w not in out:
            out.append(w)
    return out[:12]


def _looks_like_drug_monograph(title: str, snippet: str, url: str) -> bool:
    t = _safe_str(title).lower()
    s = _safe_str(snippet).lower()
    u = _safe_str(url).lower()

    if "drug information" in t:
        return True
    if "drug information" in s[:240]:
        return True
    if "medication guide" in t or "medication guide" in s[:240]:
        return True
    if "drug monograph" in t or "drug monograph" in s[:240]:
        return True
    if re.match(r"^[a-z0-9][a-z0-9\s\-/()]{1,80}:\s*medlineplus drug information$", t):
        return True
    if "/drug-information/" in u:
        return True
    return False


def _item_mentions_topic(item: Dict[str, Any], topic_terms: List[str]) -> bool:
    if not topic_terms:
        return True

    blob = " ".join(
        [
            _safe_str(item.get("title")),
            _safe_str(item.get("snippet")),
            _safe_str(item.get("url")),
        ]
    ).lower()

    for term in topic_terms:
        if term and term in blob:
            return True
    return False


def _drop_obviously_offtopic_items(state: Any, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Very cheap post-retrieval cleanup only.
    Reject only clearly off-topic drug monograph pages that do not mention
    the current complaint/diagnosis/pathway terms at all.
    """
    topic_terms = _topic_terms(state)
    if not items:
        return []

    kept: List[Dict[str, Any]] = []
    for it in items:
        title = _safe_str(it.get("title"))
        snippet = _safe_str(it.get("snippet"))
        url = _safe_str(it.get("url"))

        if _looks_like_drug_monograph(title, snippet, url) and not _item_mentions_topic(it, topic_terms):
            continue

        kept.append(it)

    return kept


def _select_min4_capped(items: List[Dict[str, Any]], target_n: int = 5, min_n: int = 4) -> List[Dict[str, Any]]:
    def dom(it: Dict[str, Any]) -> str:
        return _domain_group_from_url(_safe_str(it.get("url")))

    cleaned: List[Dict[str, Any]] = []
    seen_key: set[str] = set()
    for it in items or []:
        title = _safe_str(it.get("title"))
        url = _safe_str(it.get("url"))
        if not title and not url:
            continue
        key = url or title
        if key in seen_key:
            continue
        seen_key.add(key)
        cleaned.append(it)

    out: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}

    for it in cleaned:
        d = dom(it)
        if counts.get(d, 0) >= 1:
            continue
        counts[d] = 1
        out.append(it)
        if len(out) >= target_n:
            break

    if len(out) < min_n:
        for it in cleaned:
            if it in out:
                continue
            d = dom(it)
            if counts.get(d, 0) >= 2:
                continue
            counts[d] = counts.get(d, 0) + 1
            out.append(it)
            if len(out) >= min_n:
                break

    if len(out) < target_n:
        for it in cleaned:
            if it in out:
                continue
            d = dom(it)
            if counts.get(d, 0) >= 2:
                continue
            counts[d] = counts.get(d, 0) + 1
            out.append(it)
            if len(out) >= target_n:
                break

    return out[:target_n]


def _strip_html_to_text(html_bytes: bytes) -> str:
    try:
        s = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        try:
            s = html_bytes.decode("latin-1", errors="replace")
        except Exception:
            return ""
    s = _html.unescape(s)

    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = re.sub(r"(?i)</p\s*>", "\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?s)<.*?>", " ", s)

    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _best_snippet_from_text(text: str, max_chars: int = 900) -> str:
    if not text:
        return ""

    text = _safe_str(text)
    if not text:
        return ""

    boilerplate_pats = [
        r"copyright",
        r"all rights reserved",
        r"permission requests",
        r"downloaded from",
        r"editorial team",
        r"medical encyclopedia",
        r"reviewed by",
        r"for the private, noncommercial use",
        r"contact copyrights@",
        r"updated by:",
    ]

    positive_tokens = [
        "first-line",
        "first line",
        "recommended",
        "recommendation",
        "should",
        "strong",
        "conditional",
        "suggest",
        "use",
        "topical",
        "oral",
        "dose",
        "dosing",
        "mg",
        "mcg",
        "g ",
        "ml",
        "twice",
        "daily",
        "every",
        "days",
        "hours",
        "times daily",
        "once daily",
        "twice daily",
        "three times daily",
        "four times daily",
        "by mouth",
        "orally",
        "course",
        "duration",
        "tablet",
        "capsule",
        "apply",
        "applied",
    ]

    supportive_care_tokens = [
        "drink",
        "warm liquids",
        "cold liquids",
        "gargle",
        "rest",
        "fluids",
        "ice pop",
        "honey",
        "salt water",
        "exercise program",
        "posture",
        "stretching",
    ]

    def score_line(s: str) -> int:
        sl = f" {s.lower()} "
        score = 0

        if len(s) >= 60:
            score += 2
        if len(s) >= 120:
            score += 1

        for pat in boilerplate_pats:
            if re.search(pat, sl):
                score -= 12

        for tok in positive_tokens:
            if tok in sl:
                score += 3

        for tok in supportive_care_tokens:
            if tok in sl:
                score -= 3

        if re.search(r"\b\d+\s?(mg|mcg|g|ml)\b", sl):
            score += 8
        if re.search(r"\b(once|twice|three times|four times)\s+daily\b", sl):
            score += 7
        if re.search(r"\bevery\s+\d+\s*(hour|hours|hr|hrs)\b", sl):
            score += 7
        if re.search(r"\bfor\s+\d+\s+days?\b", sl):
            score += 7
        if re.search(r"\b\d+\s+days?\b", sl):
            score += 5
        if re.search(r"\b(by mouth|orally|topical|oral)\b", sl):
            score += 4
        if re.search(r"\b(apply|applied)\b", sl):
            score += 4
        if re.search(r"\b(treat|treatment|therapy|regimen|antibiotic|nsaid|analgesic|medication)\b", sl):
            score += 4

        return score

    raw_lines = [ln.strip(" -•\t\r") for ln in text.split("\n")]
    lines = [ln for ln in raw_lines if ln and len(ln) >= 40]

    best = ""
    best_score = -10**9

    for ln in lines:
        sc = score_line(ln)
        if sc > best_score:
            best = ln
            best_score = sc

    flat = re.sub(r"\s+", " ", text).strip()
    if flat:
        parts = re.split(r"(?<=[\.\!\?;:])\s+", flat)
        sent_chunks = [p.strip(" -•\t\r") for p in parts if p.strip()]
        for s in sent_chunks:
            if len(s) < 40:
                continue
            sc = score_line(s)
            if sc > best_score:
                best = s
                best_score = sc

    if best and best_score > 0:
        return best[:max_chars].strip()

    for ln in lines:
        low = ln.lower()
        if any(re.search(p, low) for p in boilerplate_pats):
            continue
        if len(ln) > 80:
            return ln[:max_chars].strip()

    return flat[:max_chars].strip()


def _is_probably_pdf_url(url: str) -> bool:
    u = _safe_str(url).lower()
    return ".pdf" in u


def _enrich_selected_snippets(selected: List[Dict[str, Any]]) -> None:
    """
    Fill snippet if missing by fetching the URL HTML for SELECTED items only.
    PDFs are intentionally skipped here to avoid binary/garbled snippet output.
    """
    for it in selected or []:
        snip = _safe_str(it.get("snippet"))
        url = _safe_str(it.get("url"))
        if snip or not url:
            continue

        if _is_probably_pdf_url(url):
            it["snippet"] = ""
            continue

        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "CallCareEvidenceFetcher/1.0 (+telephone-only clinical note evidence)",
                    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
                },
                method="GET",
            )
            with urlopen(req, timeout=8) as resp:
                raw = resp.read(600_000)

                content_type = ""
                try:
                    content_type = _safe_str(resp.headers.get("Content-Type", ""))
                except Exception:
                    content_type = ""

            if "application/pdf" in content_type.lower() or raw[:5] == b"%PDF-":
                it["snippet"] = ""
                continue

            text = _strip_html_to_text(raw)
            best = _best_snippet_from_text(text, max_chars=900)
            if best:
                it["snippet"] = best
        except Exception:
            continue


def _set_packet_evidence(kwargs: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> None:
    ev_payload: List[Dict[str, Any]] = []
    for it in evidence_items or []:
        url = _safe_str(it.get("url"))
        ev_payload.append(
            {
                "title": _safe_str(it.get("title")),
                "source": _safe_str(it.get("source")) or _domain_group_from_url(url),
                "url": url,
                "accessed": _safe_str(it.get("accessed")) or now_iso(),
                "snippet": _safe_str(it.get("snippet")),
            }
        )
    kwargs["evidence"] = ev_payload


def finalize(session_id: Any, note: Any, state: Any) -> Any:
    session_id_s = _safe_str(session_id) or _safe_str(getattr(state, "session_id", None)) or str(uuid.uuid4())
    packet_id = str(uuid.uuid4())

    base_queries = _get_queries_from_state(state)
    if not base_queries:
        base_queries = _run_query_llm_if_available(state)
    if not base_queries:
        base_queries = _generic_fallback_queries(state)

    expanded_queries = _expand_queries_by_publisher(base_queries, max_total=12) or base_queries

    max_urls = 40
    max_per_publisher = 2

    raw = _call_llm_http_evidence(
        state=state,
        queries=expanded_queries,
        max_urls=max_urls,
        max_per_publisher=max_per_publisher,
    )
    raw = _drop_obviously_offtopic_items(state, raw)

    selected = _select_min4_capped(raw, target_n=5, min_n=4)

    _enrich_selected_snippets(selected)

    try:
        if hasattr(note, "evidence"):
            setattr(note, "evidence", selected)
    except Exception:
        pass
    try:
        setattr(state, "evidence", selected)
    except Exception:
        pass

    kwargs: Dict[str, Any] = {
        "packet_id": packet_id,
        "created_at": now_iso(),
        "session_id": session_id_s,
        "state": state,
        "note": note,
        "pathway_id": _safe_str(getattr(state, "pathway_id", None) or ""),
    }
    _set_packet_evidence(kwargs, selected)

    packet_obj = None
    if ReviewPacket is not None:
        try:
            packet_obj = ReviewPacket(**kwargs)  # type: ignore
        except Exception:
            packet_obj = None

    out_path = FINAL_DIR / f"{packet_id}.json"
    try:
        payload = {
            "packet_id": packet_id,
            "created_at": kwargs["created_at"],
            "session_id": session_id_s,
            "pathway_id": kwargs.get("pathway_id", ""),
            "base_queries": base_queries,
            "queries": expanded_queries,
            "selected_evidence_count": len(selected),
            "evidence": kwargs.get("evidence", []),
        }
        payload["note_text"] = _safe_str(getattr(note, "text", None) or getattr(note, "soap", None) or "")
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return packet_obj if packet_obj is not None else kwargs
