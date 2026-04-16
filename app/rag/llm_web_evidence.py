import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Uses your existing fast pipeline
from app.rag.auto_acquire_fast import acquire_ingest_attach

# ---------------------------
# Configuration
# ---------------------------

# IMPORTANT: keep this tight to your truly acceptable domains.
# Add/remove as you prefer.
ALLOWED_DOMAINS = [
    "aafp.org",
    "nice.org.uk",
    "nhs.uk",
    "cdc.gov",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "medlineplus.gov",
    "accessdata.fda.gov",
]

MAX_ACQUIRE_CALLS = 4  # keep it fast
MAX_URLS_TOTAL = 8     # cap what ends up cited


# ---------------------------
# LLM hook (reuses your existing LLM plumbing if present)
# ---------------------------

def _call_llm_json(prompt: str) -> Dict[str, Any]:
    """
    Returns JSON dict. We try to reuse your project's LLM caller.
    If this raises, you need to connect it to your existing LLM function.
    """
    # Try common in-project patterns (adjust if your code uses a different entrypoint).
    # 1) app.clinical.llm_note_writer may already have a low-level caller.
    try:
        from app.clinical import llm_note_writer as lnw  # type: ignore
        for name in ("call_llm_json", "llm_json", "generate_json", "_call_llm_json"):
            fn = getattr(lnw, name, None)
            if callable(fn):
                out = fn(prompt)
                if isinstance(out, dict):
                    return out
                if isinstance(out, str):
                    return json.loads(out)
    except Exception:
        pass

    # 2) Fall back to OpenAI SDK if you have it configured (OPENAI_API_KEY etc.)
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI()
        model = os.environ.get("CALLCARE_LLM_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON. No prose."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        txt = resp.choices[0].message.content or "{}"
        return json.loads(txt)
    except Exception as e:
        raise RuntimeError(
            "No working LLM JSON caller found. "
            "Either add a JSON-capable helper in app/clinical/llm_note_writer.py "
            "or set up OpenAI SDK env (OPENAI_API_KEY). "
            f"Original error: {e}"
        )


# ---------------------------
# Utilities
# ---------------------------

_URL_RE = re.compile(r"https?://[^\s\]\)\"']+")

def _extract_urls(obj: Any) -> List[str]:
    urls: List[str] = []
    if isinstance(obj, str):
        urls.extend(_URL_RE.findall(obj))
    elif isinstance(obj, list):
        for x in obj:
            urls.extend(_extract_urls(x))
    elif isinstance(obj, dict):
        for _, v in obj.items():
            urls.extend(_extract_urls(v))
    # de-dupe preserve order
    seen = set()
    out = []
    for u in urls:
        u2 = u.strip()
        if not u2 or u2 in seen:
            continue
        seen.add(u2)
        out.append(u2)
    return out

def _domain_allowed(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in ALLOWED_DOMAINS)

def _clean_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


# ---------------------------
# Main API
# ---------------------------

def llm_guided_web_evidence(
    *,
    dx_text: str,
    chief_complaint: str,
    age_years: Optional[int],
    pregnancy_possible: str,
    renal_disease: str,
    liver_disease: str,
) -> List[Dict[str, str]]:
    """
    Returns evidence list as dicts: {"title":..., "source":..., "snippet":...}
    Uses: one LLM call -> a few web acquisitions (allowed domains only) -> returns acquired URLs.
    """
    dx = _clean_text(dx_text)
    cc = _clean_text(chief_complaint)

    # Single LLM call that outputs:
    # - queries: list[str] (max MAX_ACQUIRE_CALLS)
    # - must_use_domains: list[str] (optional)
    # - why: short strings (optional, ignored)
    prompt = f"""
You are selecting authoritative clinical sources for a telephone-only urgent care note.

Patient:
- age_years: {age_years}
- pregnancy_possible: {pregnancy_possible}
- renal_disease: {renal_disease}
- liver_disease: {liver_disease}

Clinical:
- diagnosis (Assessment): {dx}
- chief complaint: {cc}

You MUST produce JSON with:
{{
  "queries": [
    "...",
    "..."
  ]
}}

Rules:
- Only produce up to {MAX_ACQUIRE_CALLS} queries.
- Queries must be designed to find: outpatient management + medication options + dosing + red flags/referral.
- Queries must stay general and scalable (no hand-crafted anatomy lists, no per-diagnosis hardcoding).
- Prefer AAFP, NICE, NHS, CDC, NCBI/PMC, MedlinePlus, FDA label where appropriate.
- Include renal/hepatic/pregnancy considerations in the queries when relevant.
Return ONLY JSON.
""".strip()

    j = _call_llm_json(prompt)
    queries = j.get("queries") if isinstance(j, dict) else None
    if not isinstance(queries, list) or not queries:
        # fallback deterministic queries
        queries = [
            f"{dx} outpatient management guideline",
            f"{dx} first line medication dosing",
            f"{dx} alternative medication dosing contraindications renal hepatic pregnancy",
            f"{dx} red flags when to refer imaging",
        ]
    queries = [str(q).strip() for q in queries if str(q).strip()]
    queries = queries[:MAX_ACQUIRE_CALLS]

    acquired_urls: List[str] = []

    # Run acquisitions (fast pipeline). This searches + ingests into your library.
    for q in queries:
        try:
            res = acquire_ingest_attach(
                query=q,
                allowed_domains=ALLOWED_DOMAINS,
                max_urls=3,           # keep fast
                max_ingest=2,         # keep fast
                timeout_sec=18,       # keep fast
            )
        except TypeError:
            # If your acquire_ingest_attach signature differs, call it with fewer args.
            res = acquire_ingest_attach(q)

        urls = _extract_urls(res)
        # Keep only allowed domains
        urls = [u for u in urls if _domain_allowed(u)]
        for u in urls:
            if u not in acquired_urls:
                acquired_urls.append(u)
        if len(acquired_urls) >= MAX_URLS_TOTAL:
            break

    # Convert to evidence dicts (title will be filled later by your ingest metadata if available;
    # but this at least ensures Evidence Used shows real URLs, not FAISS neighbors).
    evidence: List[Dict[str, str]] = []
    for u in acquired_urls[:MAX_URLS_TOTAL]:
        evidence.append({
            "title": "",   # optional; your adapter may fill title from stored chunk
            "source": u,
            "snippet": "",
        })
    return evidence
