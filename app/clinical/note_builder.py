from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from app.clinical.evidence_utils import ensure_evidence_dict
from app.core.models import CallState, NoteDraft


# ------------------------------------------------------------
# WEB-ONLY NOTE BUILDER (thin)
# ------------------------------------------------------------
# - Does NOT retrieve evidence
# - Does NOT scan library / YAML / manifests
# - Does NOT auto-acquire / ingest / reindex
# - Does NOT hard-code anatomy/diagnosis/meds/dosing
# - Only builds SOAP text via llm_note_writer using evidence already attached
#
# Compatibility: review_queue imports now_iso from here.
# ------------------------------------------------------------


def now_iso() -> str:
    """UTC timestamp string for compatibility with review_queue imports."""
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def _safe_str(x: Any) -> str:
    try:
        return str(x if x is not None else "").strip()
    except Exception:
        return ""


def _get_answers_dict(state: Any) -> Dict[str, Any]:
    if state is None:
        return {}
    try:
        a = getattr(state, "answers", None)
        if isinstance(a, dict):
            return a
    except Exception:
        pass
    try:
        sx = getattr(state, "symptoms", None) or {}
        if isinstance(sx, dict):
            a = sx.get("_answers")
            if isinstance(a, dict):
                return a
    except Exception:
        pass
    return {}


def _get_existing_evidence_from_state(state: Any, max_items: int = 10) -> List[Dict[str, Any]]:
    """
    Pass-through evidence only. No retrieval here.
    Expected dict shape (web-only): {title, source, url, accessed, snippet}
    """
    candidates: List[Any] = []

    try:
        ev = getattr(state, "evidence_used", None)
        if isinstance(ev, list):
            candidates = ev
    except Exception:
        pass

    if not candidates:
        ans = _get_answers_dict(state)
        ev2 = ans.get("evidence") or ans.get("evidence_used")
        if isinstance(ev2, list):
            candidates = ev2

    out: List[Dict[str, Any]] = []
    for e in candidates[: max_items * 2]:
        if e is None:
            continue
        try:
            d = ensure_evidence_dict(e)
            title = _safe_str(d.get("title") or "")
            source = _safe_str(d.get("source") or "Source")
            url = _safe_str(d.get("url") or "")
            accessed = _safe_str(d.get("accessed") or "")
            snippet = _safe_str(d.get("snippet") or d.get("text") or "")

            if not title and not url:
                continue

            out.append(
                {
                    "title": title or (url[:160] if url else "Source"),
                    "source": source or "Source",
                    "url": url,
                    "accessed": accessed,
                    "snippet": snippet,
                }
            )
        except Exception:
            continue

        if len(out) >= max_items:
            break

    return out


def _fallback_soap_from_state(state: Any) -> str:
    cc = ""
    try:
        cc = _safe_str(getattr(state, "chief_complaint", "") or getattr(state, "chief", "") or "")
    except Exception:
        cc = ""

    lines = []
    lines.append("S: " + (cc or "Chief complaint not provided."))
    lines.append("")
    lines.append("O: (Phone visit; no vitals/exam documented.)")
    lines.append("")
    lines.append("A: (Assessment pending.)")
    lines.append("")
    lines.append("P:")
    lines.append("- (Plan pending.)")
    return "\n".join(lines).strip() + "\n"


def _generate_llm_soap(state: CallState, evidence: List[Dict[str, Any]]) -> Tuple[str, str]:
    if os.environ.get("CALLCARE_NO_LLM", "").strip().lower() in ("1", "true", "yes", "on"):
        return ("", "CALLCARE_NO_LLM enabled")

    try:
        import importlib

        llm = importlib.import_module("app.clinical.llm_note_writer")
        build_prompt = getattr(llm, "build_prompt", None)
        generate_note_text = getattr(llm, "generate_note_text", None)

        if not callable(build_prompt) or not callable(generate_note_text):
            return ("", "llm_note_writer missing build_prompt/generate_note_text")

        prompt = build_prompt(state=state, evidence=evidence)
        txt = generate_note_text(prompt) or ""
        txt = _safe_str(txt)
        if not txt:
            return ("", "empty LLM output")

        # If the model returns JSON, extract soap_text.
        # IMPORTANT: By default, do NOT persist NOTE-LLM evidence_search_queries into state/_answers.
        # Rationale: finalize.py prioritizes _answers["evidence_search_queries"]; persisting NOTE-LLM queries
        # makes evidence retrieval sensitive to note prompt changes and can cause unstable evidence quality.
        #
        # To explicitly allow persisting NOTE-LLM queries (for experiments only), set:
        #   CALLCARE_ACCEPT_NOTE_LLM_QUERIES=1
        if txt.startswith("{") and txt.endswith("}"):
            try:
                obj = json.loads(txt)
                if isinstance(obj, dict):
                    soap = _safe_str(obj.get("soap_text") or obj.get("soap") or "")
                    if soap:
                        txt = soap

                    accept_note_llm_qs = os.environ.get("CALLCARE_ACCEPT_NOTE_LLM_QUERIES", "").strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                    if accept_note_llm_qs:
                        qs = obj.get("evidence_search_queries")
                        if isinstance(qs, list):
                            qs2 = [_safe_str(q) for q in qs if _safe_str(q)]
                            if qs2:
                                ans = _get_answers_dict(state)
                                ans["evidence_search_queries"] = qs2[:4]
                                try:
                                    if hasattr(state, "answers"):
                                        state.answers = ans
                                except Exception:
                                    pass
                                try:
                                    sx = getattr(state, "symptoms", None)
                                    if isinstance(sx, dict):
                                        a = sx.get("_answers")
                                        if isinstance(a, dict):
                                            a["evidence_search_queries"] = qs2[:4]
                                            sx["_answers"] = a
                                except Exception:
                                    pass
            except Exception:
                pass

        return (txt.strip() + "\n", "")
    except Exception as e:
        return ("", f"{type(e).__name__}: {_safe_str(e)[:200]}")


def build_note(state: CallState) -> NoteDraft:
    evidence = _get_existing_evidence_from_state(state, max_items=10)
    soap_text, err = _generate_llm_soap(state, evidence)

    if not soap_text.strip():
        soap_text = _fallback_soap_from_state(state)
        if err:
            soap_text = f"(NOTE: LLM note generation unavailable: {err})\n" + soap_text

    return NoteDraft(
        soap=soap_text,
        patient_instructions=[],
        clinician_questions=[],
        risk_flags=[],
        evidence=evidence,
    )


def build_note_with_reason(state: CallState, *args, **kwargs):
    note = build_note(state)
    return note, None


def fallback_note_from_state(state: CallState) -> NoteDraft:
    return NoteDraft(
        soap=_fallback_soap_from_state(state),
        patient_instructions=[],
        clinician_questions=[],
        risk_flags=[],
        evidence=_get_existing_evidence_from_state(state, max_items=10),
    )
