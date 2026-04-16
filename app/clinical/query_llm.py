import os
import json
from typing import List, Optional, Any, Dict

from openai import OpenAI


QUERYONLY_SYSTEM = """You generate web search queries for clinical evidence retrieval.
Return ONLY valid JSON (no markdown, no commentary).

Rules:
- Output JSON: {"queries": ["...","...","..."]}
- Always return 2–4 queries.
- Each query <= 12 words.
- No URLs. No site: operators.
- Must include at least:
  1) diagnosis/condition management guideline query
  2) treatment/medication/dosing adult query (mg, duration, contraindications)
  3) red flags / return precautions or testing query
- Use provided case context only; do not invent details.
""".strip()


def _to_dict(x: Any) -> Optional[Dict[str, Any]]:
    if x is None:
        return None
    if isinstance(x, dict):
        return x
    try:
        if hasattr(x, "model_dump"):
            d = x.model_dump()
            return d if isinstance(d, dict) else None
    except Exception:
        pass
    try:
        d = dict(x)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _case_context(state) -> str:
    cc = ""
    try:
        cc = str(getattr(state, "chief_complaint", "") or "").strip()
    except Exception:
        cc = ""

    sx = _to_dict(getattr(state, "symptoms", None))
    ans = None
    if isinstance(sx, dict):
        a = sx.get("_answers")
        if isinstance(a, dict):
            ans = a

    # Also allow state.answers if present
    st_ans = None
    try:
        st_ans = getattr(state, "answers", None)
    except Exception:
        st_ans = None
    if not isinstance(ans, dict) and isinstance(st_ans, dict):
        ans = st_ans

    # Intake extract/summary if present
    intake_extract = ""
    intake_summary = ""
    if isinstance(sx, dict):
        intake_extract = str(sx.get("_llm_intake_extract") or "").strip()
        intake_summary = str(sx.get("_llm_intake_summary") or "").strip()

    # Safety flags
    preg = ""
    renal = ""
    liver = ""
    try:
        preg = str(getattr(state, "pregnancy_possible", "") or "").strip()
    except Exception:
        preg = ""
    if isinstance(ans, dict):
        renal = str(ans.get("renal_disease") or "").strip()
        liver = str(ans.get("liver_disease") or "").strip()

    # Compact answered fields
    answered_lines = []
    if isinstance(ans, dict):
        for k in sorted(ans.keys()):
            v = ans.get(k)
            if v is None:
                continue
            sv = str(v).strip()
            if sv:
                answered_lines.append(f"- {k}: {sv}")

    answered_block = "\n".join(answered_lines[:80]) if answered_lines else "(none)"

    parts = [
        f"CHIEF_COMPLAINT: {cc or '(missing)'}",
        "ANSWERED_FIELDS:",
        answered_block,
    ]
    if intake_extract:
        parts += ["LLM_INTAKE_EXTRACT:", intake_extract[:1200]]
    if intake_summary:
        parts += ["LLM_INTAKE_SUMMARY:", intake_summary[:1200]]
    parts += [
        f"FLAGS: pregnancy_possible={preg or '(unknown)'} renal_disease={renal or '(unknown)'} liver_disease={liver or '(unknown)'}"
    ]
    return "\n".join(parts)


def generate_evidence_queries(state, max_queries: int = 4) -> List[str]:
    # If already present, respect it
    try:
        existing = getattr(state, "evidence_search_queries", None)
        if isinstance(existing, list) and len(existing) >= 2:
            return [str(x).strip() for x in existing if str(x).strip()][:max_queries]
    except Exception:
        pass

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return []

    # Separate model for speed; falls back to CALLCARE_LLM_MODEL; final fallback gpt-4.1-mini
    model = os.getenv("CALLCARE_QUERY_MODEL", "").strip() or os.getenv("CALLCARE_LLM_MODEL", "").strip() or "gpt-4.1-mini"

    client = OpenAI(api_key=api_key)
    user_prompt = _case_context(state) + "\n\nReturn JSON now."

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": QUERYONLY_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_output_tokens=220,
        )
        text = getattr(resp, "output_text", "") or ""
    except Exception:
        return []

    # Parse JSON robustly
    txt = (text or "").strip()
    start = txt.find("{")
    end = txt.rfind("}")
    if start != -1 and end != -1 and end > start:
        txt = txt[start:end+1]
    try:
        obj = json.loads(txt)
    except Exception:
        return []

    qs = obj.get("queries")
    if not isinstance(qs, list):
        return []

    out = []
    for q in qs:
        q = str(q).strip()
        if not q:
            continue
        # enforce <=12 words
        words = q.split()
        if len(words) > 12:
            q = " ".join(words[:12])
        out.append(q)

    # Enforce 2–4
    out = out[:max_queries]
    if len(out) < 2:
        return []

    # Persist to state
    try:
        setattr(state, "evidence_search_queries", out)
    except Exception:
        pass
    try:
        st_ans = getattr(state, "answers", None)
        if isinstance(st_ans, dict):
            st_ans["evidence_search_queries"] = out
    except Exception:
        pass
    try:
        sx = _to_dict(getattr(state, "symptoms", None))
        if isinstance(sx, dict):
            sx["evidence_search_queries"] = out
            if isinstance(sx.get("_answers"), dict):
                sx["_answers"]["evidence_search_queries"] = out
            try:
                setattr(state, "symptoms", sx)
            except Exception:
                pass
    except Exception:
        pass

    return out
