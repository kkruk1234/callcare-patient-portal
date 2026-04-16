import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import urllib.request

# ---- Config ----

ALLOWLIST_DOMAINS = {
    # Guidelines / authoritative
    "www.nhs.uk",
    "www.nice.org.uk",
    "www.aafp.org",
    "www.cdc.gov",
    "www.nih.gov",
    "www.niams.nih.gov",
    "medlineplus.gov",
    "www.ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    # FDA labels
    "www.accessdata.fda.gov",
    "accessdata.fda.gov",
    # Optional orgs (add if you want)
    "idsociety.org",
    "www.idsociety.org",
}

# Keep it tight for speed
HTTP_TIMEOUT_SEC = 12
MAX_BYTES = 2_500_000  # ~2.5MB cap per fetch to keep runtime sane

PACK_DIR = Path("data/packs")
PACK_DIR.mkdir(parents=True, exist_ok=True)

# ---- Minimal LLM client wrapper ----
# We try to reuse whatever you already have. If not found, we fall back to OpenAI via HTTPS.
def _call_llm_json(system: str, user: str, *, max_tokens: int = 700) -> Dict[str, Any]:
    """
    Returns a parsed JSON dict.
    Priority:
      1) app.clinical.llm_note_writer.call_llm_json (if exists)
      2) app.clinical.llm_client.call_llm_json (if exists)
      3) OpenAI Responses API via HTTPS (requires OPENAI_API_KEY)
    """
    # 1)
    try:
        from app.clinical import llm_note_writer as l
        fn = getattr(l, "call_llm_json", None) or getattr(l, "_call_llm_json", None)
        if callable(fn):
            return fn(system=system, user=user, max_tokens=max_tokens)
    except Exception:
        pass

    # 2)
    try:
        from app.clinical import llm_client as c
        fn = getattr(c, "call_llm_json", None)
        if callable(fn):
            return fn(system=system, user=user, max_tokens=max_tokens)
    except Exception:
        pass

    # 3) OpenAI HTTPS fallback
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("No LLM client found and OPENAI_API_KEY is not set.")

    model = os.environ.get("CALLCARE_LLM_MODEL", "").strip() or "gpt-4.1-mini"

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_output_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)

    # Responses API returns output in a structured list; pull any JSON text content.
    text = ""
    for item in (data.get("output") or []):
        for c in (item.get("content") or []):
            if c.get("type") in ("output_text", "text"):
                text += c.get("text", "")
    text = text.strip()
    if not text:
        raise RuntimeError("LLM returned empty response.")
    try:
        return json.loads(text)
    except Exception:
        # Sometimes model returns already-parsed structure; attempt fallback:
        if isinstance(data, dict) and isinstance(data.get("output_json"), dict):
            return data["output_json"]
        raise

# ---- Helpers ----

def _is_allowed_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = (p.netloc or "").lower()
        return host in ALLOWLIST_DOMAINS
    except Exception:
        return False

def _dedupe_preserve(urls: List[str]) -> List[str]:
    seen = set()
    out = []
    for u in urls:
        u = (u or "").strip()
        if not u:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out

def _fetch_url(url: str) -> Dict[str, Any]:
    """
    Fetch HTML/PDF bytes with a hard cap.
    Returns dict with: ok, url, content_type, bytes_len
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CallCare/1.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {"ok": False, "url": url, "error": f"too_large>{MAX_BYTES}", "content_type": ct, "bytes_len": len(raw)}
        return {"ok": True, "url": url, "content_type": ct, "bytes_len": len(raw), "bytes": raw}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}

def _dx_from_note(note) -> str:
    soap = str(getattr(note, "soap", "") or "")
    # Prefer A: section
    m = re.search(r"(?ims)\nA:\s*\n(.*?)(?:\n\n|\nP:\s*\n)", soap)
    if m:
        lines = [ln.strip() for ln in (m.group(1) or "").splitlines() if ln.strip()]
        if lines:
            return " ".join(lines[0].split()[:24]).strip()
    m2 = re.search(r"(?im)^\s*A:\s*(.+)$", soap)
    if m2:
        return " ".join((m2.group(1) or "").strip().split()[:24]).strip()
    # last resort: note.diagnosis if present
    for attr in ("diagnosis", "working_diagnosis", "impression", "assessment"):
        val = str(getattr(note, attr, "") or "").strip()
        if val:
            return " ".join(val.split()[:24]).strip()
    return ""

def _patient_context(state) -> str:
    if state is None:
        return ""
    cc = (getattr(state, "chief_complaint", "") or "").strip()
    age = getattr(state, "age_years", None) or getattr(state, "age", None)
    preg = (getattr(state, "pregnancy_possible", "") or "").strip()
    sx = getattr(state, "symptoms", None) or {}
    ren = ""
    liv = ""
    try:
        if isinstance(sx, dict):
            ren = (sx.get("renal_disease") or "").strip()
            liv = (sx.get("liver_disease") or "").strip()
    except Exception:
        pass

    bits = []
    if cc:
        bits.append(f"Chief complaint: {cc}")
    if age:
        bits.append(f"Age (years): {age}")
    if preg:
        bits.append(f"Pregnancy possible: {preg}")
    if ren:
        bits.append(f"Renal disease: {ren}")
    if liv:
        bits.append(f"Liver disease: {liv}")
    return "\n".join(bits)

def llm_select_urls(note, state, *, n_condition: int = 4, n_meds: int = 2) -> Dict[str, List[str]]:
    dx = _dx_from_note(note)
    ctx = _patient_context(state)

    system = (
        "You are a clinical evidence selector.\n"
        "You MUST output JSON with keys: condition_sources, medication_sources.\n"
        "Each value MUST be a list of URLs.\n"
        "Return EXACTLY "
        f"{n_condition} condition_sources and {n_meds} medication_sources.\n"
        "Hard constraints:\n"
        "- Only choose URLs from these allowed domains:\n"
        + "\n".join(sorted(ALLOWLIST_DOMAINS)) + "\n"
        "- Choose sources that SUPPORT THE DIAGNOSIS TEXT. Do NOT drift to adjacent common topics.\n"
        "- DO NOT select unrelated drug monographs (oncology injectables, etc.) unless diagnosis explicitly requires.\n"
        "- Prefer: condition pages/guidelines; for meds prefer FDA label (accessdata.fda.gov) or MedlinePlus Drug Information.\n"
        "- Do NOT choose generic 'chronic pain' hubs when a condition-specific guideline exists.\n"
        "- If diagnosis is chest wall/rib/costochondral strain, do NOT choose sciatica/low-back/sciatica guidelines.\n"
        "  (This is not a per-diagnosis cheat list; it is a semantic mismatch rule.)\n"
        "- If you are unsure, choose the closest condition page within allowed domains.\n"
    )

    user = (
        f"DIAGNOSIS (from Assessment):\n{dx}\n\n"
        f"PATIENT CONTEXT:\n{ctx}\n\n"
        "Task:\n"
        f"1) Pick {n_condition} best CONDITION-MANAGEMENT sources (evaluation + outpatient treatment guidance).\n"
        f"2) Pick {n_meds} best MEDICATION sources that contain DOSING + contraindications/precautions.\n"
        "Return only JSON."
    )

    out = _call_llm_json(system=system, user=user, max_tokens=650)

    cond = _dedupe_preserve([str(u) for u in (out.get("condition_sources") or [])])
    meds = _dedupe_preserve([str(u) for u in (out.get("medication_sources") or [])])

    # Enforce exact counts by trimming (we will re-ask if too few after validation)
    cond = cond[:n_condition]
    meds = meds[:n_meds]
    return {"condition_sources": cond, "medication_sources": meds}

def acquire_ingest_attach_llm(note, state, *, min_total: int = 3) -> Tuple[List[Any], Dict[str, Any]]:
    """
    Returns (EvidenceRefs, debug_info).
    Uses LLM to pick URLs, then uses existing ingestion pipeline if available.
    """
    attempt = 0
    debug = {"attempts": []}
    refs: List[Any] = []

    while attempt < 2:
        attempt += 1
        sel = llm_select_urls(note, state, n_condition=4, n_meds=2)

        cond = [u for u in sel["condition_sources"] if _is_allowed_url(u)]
        meds = [u for u in sel["medication_sources"] if _is_allowed_url(u)]
        urls = _dedupe_preserve(cond + meds)

        step = {"attempt": attempt, "picked_total": len(urls), "picked": urls, "fetched": []}

        # Fetch (for logging + to avoid dead links). We still rely on ingestion for storage/indexing.
        for u in urls:
            step["fetched"].append(_fetch_url(u))

        debug["attempts"].append(step)

        # Try to use your ingestion + EvidenceRef adapter
        refs = []
        try:
            from app.rag.auto_acquire_fast import acquire_ingest_attach
            # Try common signature patterns
            try:
                result = acquire_ingest_attach(note=note, state=state, urls=urls)
            except TypeError:
                try:
                    result = acquire_ingest_attach(urls, note, state)
                except TypeError:
                    result = acquire_ingest_attach(urls=urls)

            # Normalize outputs
            if isinstance(result, dict):
                refs = result.get("evidence") or result.get("refs") or []
            elif isinstance(result, (list, tuple)):
                # sometimes returns (refs, meta)
                refs = result[0] if result else []
            else:
                refs = []

        except Exception as e:
            debug["ingest_error"] = str(e)
            refs = []

        # If ingestion returned usable refs, done
        if isinstance(refs, list) and len(refs) >= min_total:
            return refs, debug

        # Otherwise: write a pack log anyway (so you can inspect what LLM chose)
        try:
            pack = {
                "ts": time.time(),
                "kind": "llm_http_allowlist_acquire",
                "diagnosis": _dx_from_note(note),
                "urls": urls,
                "debug": step,
            }
            pack_path = PACK_DIR / f"llm_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}.json"
            pack_path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
            debug["last_pack_path"] = str(pack_path)
        except Exception:
            pass

        # If too few, loop once more (2 tries max)
        if attempt >= 2:
            break

    return (refs or []), debug

def llm_generate_plan(note, state, evidence_refs: List[Any]) -> str:
    """
    Generate Plan text (P:) using evidence_refs as citations/context.
    Output MUST be bullet-structured exactly as requested.
    """
    dx = _dx_from_note(note)
    ctx = _patient_context(state)

    # Build a compact evidence list
    ev_lines = []
    for e in (evidence_refs or [])[:10]:
        title = str(getattr(e, "title", "") or "")
        src = str(getattr(e, "source", "") or getattr(e, "url", "") or "")
        if title or src:
            ev_lines.append(f"- {title} | {src}".strip())
    ev_blob = "\n".join(ev_lines) if ev_lines else "(none)"

    system = (
        "You are a clinician writing a telephone-only urgent-care plan.\n"
        "You MUST produce a Plan section only, as bullet points.\n"
        "Format requirements (strict):\n"
        "- First-line treatment:\n"
        "  - <Medication name> <dose> <route> <frequency>; <max daily dose>. <1 short prescriber note: key contraindications/cautions relevant to patient>\n"
        "  - <Non-med measure(s)> (heat/ice/activity advice as appropriate)\n"
        "- Alternatives:\n"
        "  - <Medication name> <dose> <route> <frequency>; <max daily dose>. <1 short prescriber note>\n"
        "  - <topical option if appropriate> ...\n"
        "- Safety / exclusions (brief):\n"
        "  - 2–4 bullets: renal, liver, pregnancy, GI bleed/ulcer risk, anticoagulants, etc as relevant.\n"
        "- Testing (telephone-only):\n"
        "  - If testing is needed now -> specify in-person urgent care/ED.\n"
        "  - Otherwise, specify criteria/timeframe for in-person evaluation.\n"
        "- Return precautions:\n"
        "  - Specific symptoms + timeframe (today/within 24–48h/within 1 week) NOT blanket 'see in-person'.\n"
        "Rules:\n"
        "- NEVER say 'return to clinic'.\n"
        "- Do NOT invent evidence. Use common standard dosing ONLY if evidence list is empty.\n"
        "- If patient does NOT have liver disease, do NOT apply liver-dose caps.\n"
        "- Prefer standard adult dosing if age suggests adult; otherwise use weight-based pediatric dosing ONLY if weight/age clearly indicates child.\n"
    )

    user = (
        f"DIAGNOSIS:\n{dx}\n\n"
        f"PATIENT CONTEXT:\n{ctx}\n\n"
        f"EVIDENCE SOURCES (use these to guide meds/dosing/exclusions):\n{ev_blob}\n\n"
        "Write the Plan now."
    )

    out = _call_llm_json(system=system, user=user, max_tokens=900)

    # Allow either {"plan": "..."} or {"text":"..."} or raw structure
    plan = ""
    if isinstance(out, dict):
        for k in ("plan", "text", "output"):
            if isinstance(out.get(k), str) and out.get(k).strip():
                plan = out.get(k).strip()
                break
    if not plan:
        # last resort: stringify dict
        plan = json.dumps(out, indent=2)

    return plan.strip()
