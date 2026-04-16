from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable, Tuple

from app.clinical.llm_intake_planner import (
    Demographics,
    EvidenceChunk,
    build_question_plan,
    IntakePlannerError,
)
from app.clinical.plan_validator import validate_and_normalize_plan
from app.clinical.generic_intake_fallback import build_generic_fallback_plan
from app.clinical.med_exclusions_parser import parse_med_exclusions, MedExclusionFlags


@dataclass(frozen=True)
class OrchestratorConfig:
    top_n: int = 10
    max_questions: int = 12
    # Additional evidence used after intake to support plan/med generation:
    post_intake_top_n: int = 12


@dataclass
class RagIntakeArtifacts:
    # Evidence used for QUESTION PLANNING (broad)
    evidence: List[EvidenceChunk]
    plan: Dict[str, Any]
    plan_mode: str  # "rag" | "fallback"
    validation_reason: Optional[str]
    index_version: Optional[str] = None
    # Evidence refined AFTER intake (narrow, dx/med-focused) — optional
    post_intake_evidence: Optional[List[EvidenceChunk]] = None


def _pick_retrieve_fn(mod) -> Callable[..., Any]:
    """
    Finds a likely retrieval function in app.rag.retrieve without hardcoding a name.
    """
    candidates = [
        "retrieve_evidence_chunks",
        "retrieve_chunks",
        "retrieve_evidence",
        "retrieve",
        "search",
        "query",
    ]
    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn

    for name in dir(mod):
        if "retriev" in name.lower() or "search" in name.lower() or "query" in name.lower():
            fn = getattr(mod, name, None)
            if callable(fn):
                return fn

    raise RuntimeError("Could not find a retrieval function in app.rag.retrieve")


def _call_retrieve(fn: Callable[..., Any], query: str, top_n: int, metadata: Dict[str, Any]) -> Any:
    """
    Calls retrieval function using signature inspection so we don't guess param names.
    Supports common conventions: (query=, top_n=/top_k=, k=, metadata=, filters=).
    """
    sig = inspect.signature(fn)
    kwargs: Dict[str, Any] = {}

    # Map query argument
    if "query" in sig.parameters:
        kwargs["query"] = query
    elif "text" in sig.parameters:
        kwargs["text"] = query
    elif "q" in sig.parameters:
        kwargs["q"] = query
    else:
        params = list(sig.parameters.values())
        if params and params[0].kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            return fn(query)

    # Map top-N argument
    for kname in ["top_n", "top_k", "k", "n", "limit"]:
        if kname in sig.parameters:
            kwargs[kname] = int(top_n)
            break

    # Map metadata / filters
    for mname in ["metadata", "meta", "filters", "filter", "context"]:
        if mname in sig.parameters:
            kwargs[mname] = metadata
            break

    return fn(**kwargs)


def _convert_retrieval_to_chunks(raw: Any) -> List[EvidenceChunk]:
    """
    Expected raw format: list[dict] with keys like:
      - text
      - title
      - url
      - source/publisher
      - date (optional)
    """
    if raw is None:
        return []

    if isinstance(raw, dict) and "chunks" in raw and isinstance(raw["chunks"], list):
        raw_list = raw["chunks"]
    elif isinstance(raw, list):
        raw_list = raw
    else:
        return []

    out: List[EvidenceChunk] = []
    i = 1
    for rc in raw_list:
        if not isinstance(rc, dict):
            continue
        text = (rc.get("text") or rc.get("chunk") or rc.get("content") or "").strip()
        url = (rc.get("url") or rc.get("source_url") or rc.get("source") or "").strip()
        title = (rc.get("title") or rc.get("source_title") or "Untitled").strip()
        publisher = (rc.get("publisher") or rc.get("source") or rc.get("host") or "Unknown").strip()
        date = rc.get("date") or rc.get("published") or rc.get("updated") or None
        date = str(date).strip() if date else None

        if not text:
            continue

        out.append(
            EvidenceChunk(
                key=f"EVID_{i}",
                title=title,
                url=url,
                publisher=publisher,
                date=date,
                text=text,
            )
        )
        i += 1

    return out


def _dedupe_chunks(chunks: List[EvidenceChunk], max_n: int) -> List[EvidenceChunk]:
    """
    Deduplicate by URL (primary) then (title+publisher) fallback.
    Keeps first occurrence (retrieval order is assumed meaningful).
    """
    seen_url = set()
    seen_tp = set()
    out: List[EvidenceChunk] = []
    for c in chunks:
        u = (c.url or "").strip().lower()
        tp = ((c.title or "").strip().lower(), (c.publisher or "").strip().lower())
        if u:
            if u in seen_url:
                continue
            seen_url.add(u)
        else:
            if tp in seen_tp:
                continue
            seen_tp.add(tp)
        out.append(c)
        if len(out) >= max_n:
            break
    # Re-key deterministically
    final: List[EvidenceChunk] = []
    for i, c in enumerate(out, 1):
        final.append(
            EvidenceChunk(
                key=f"EVID_{i}",
                title=c.title,
                url=c.url,
                publisher=c.publisher,
                date=c.date,
                text=c.text,
            )
        )
    return final


def run_retrieval(query: str, demographics: Demographics, top_n: int, extra_meta: Optional[Dict[str, Any]] = None) -> List[EvidenceChunk]:
    import app.rag.retrieve as retrieve_mod

    fn = _pick_retrieve_fn(retrieve_mod)

    meta = {
        "age_years": demographics.age_years,
        "pregnancy_possible": demographics.pregnancy_possible,
    }
    if extra_meta:
        meta.update(extra_meta)

    raw = _call_retrieve(fn=fn, query=query, top_n=top_n, metadata=meta)
    chunks = _convert_retrieval_to_chunks(raw)
    return _dedupe_chunks(chunks, max_n=top_n)


def _build_post_intake_query_llm(
    llm_call_fn,
    chief: str,
    demographics: Demographics,
    structured_answers: Dict[str, Any],
    med_flags: MedExclusionFlags,
) -> str:
    """
    Build a *single* focused retrieval query string (keywords only) for Pull 2.
    This does NOT change intake questions; it only uses what you already collected.
    """
    # Keep this prompt very constrained so output is predictable.
    age = demographics.age_years
    preg = (demographics.pregnancy_possible or "").strip().lower()
    allergies = str(structured_answers.get("allergies", "") or structured_answers.get("allergies_text", "") or "").strip()

    # Pull likely diagnosis text if present (best effort; no assumptions about schema)
    dx = ""
    for k in ("working_diagnosis", "diagnosis", "assessment", "impression", "problem", "chief_dx"):
        v = structured_answers.get(k)
        if isinstance(v, str) and v.strip():
            dx = v.strip()
            break

    # Summarize symptoms if present
    summary = ""
    for k in ("summary", "intake_summary", "hpi_summary"):
        v = structured_answers.get(k)
        if isinstance(v, str) and v.strip():
            summary = v.strip()
            break

    prompt = f"""
You are generating a SINGLE search query (keywords only) to retrieve authoritative clinical guidance snippets.

Rules:
- Output ONE LINE ONLY.
- No punctuation besides spaces and hyphens.
- Include 6 to 18 keywords.
- Prefer guideline terms: guideline management treatment first line dosing contraindication.
- If a working diagnosis is available, include it.
- Always include the original chief complaint terms.

Context:
CHIEF: {chief}
DX: {dx}
SUMMARY: {summary}
AGE_YEARS: {age}
PREGNANCY_POSSIBLE: {preg}
ALLERGIES_TEXT: {allergies}

Medication safety flags:
PREG_RISK: {getattr(med_flags, "pregnancy_risk", "")}
RENAL_RISK: {getattr(med_flags, "renal_risk", "")}
HEPATIC_RISK: {getattr(med_flags, "hepatic_risk", "")}
NSAID_AVOID: {getattr(med_flags, "avoid_nsaids", "")}
ACE_ARB_AVOID: {getattr(med_flags, "avoid_ace_arb", "")}
PCN_ALLERGY: {getattr(med_flags, "penicillin_allergy", "")}

Now output the ONE LINE query:
""".strip()

    try:
        out = llm_call_fn(prompt)
    except Exception:
        out = ""

    q = str(out or "").strip()
    # Hard clamp if model misbehaves
    q = " ".join(q.split())
    if not q:
        # Deterministic fallback: still helps dosing retrieval
        base = chief.strip()
        tail = "guideline management treatment first line dosing contraindication"
        return (base + " " + tail).strip()
    return q


def run_post_intake_two_pull(
    chief: str,
    demographics: Demographics,
    structured_answers: Dict[str, Any],
    llm_call_fn,
    cfg: OrchestratorConfig,
) -> List[EvidenceChunk]:
    """
    Pull 1: broad (chief + safety/triage)
    Pull 2: narrow (dx/plan + med exclusions + dosing when relevant)
    Merge and return deduped chunks.
    """
    # Compute med flags from structured answers (kidney/liver/preg/allergies)
    pregnancy_possible = str(getattr(demographics, "pregnancy_possible", "") or "")
    allergies_text = str(structured_answers.get("allergies", "") or structured_answers.get("allergies_text", "") or "")
    med_flags = compute_med_flags(structured_answers, pregnancy_possible=pregnancy_possible, allergies_text=allergies_text)

    # Pull 1 query: keep broad but anchored
    q1 = f"{chief} telephone triage red flags guideline".strip()
    ev1 = run_retrieval(q1, demographics, top_n=max(6, cfg.post_intake_top_n // 2))

    # Pull 2 query: LLM-generated keywords with exclusions
    q2 = _build_post_intake_query_llm(llm_call_fn, chief, demographics, structured_answers, med_flags)
    ev2 = run_retrieval(q2, demographics, top_n=cfg.post_intake_top_n, extra_meta={
        "renal_risk": getattr(med_flags, "renal_risk", ""),
        "hepatic_risk": getattr(med_flags, "hepatic_risk", ""),
        "pregnancy_risk": getattr(med_flags, "pregnancy_risk", ""),
    })

    # Merge rule: keep a couple from ev1, more from ev2
    merged: List[EvidenceChunk] = []
    merged.extend(ev1[:2])
    merged.extend(ev2)

    return _dedupe_chunks(merged, max_n=cfg.post_intake_top_n)


def build_plan(
    chief: str,
    demographics: Demographics,
    llm_call_fn,
    cfg: OrchestratorConfig,
) -> RagIntakeArtifacts:
    # Evidence used to plan QUESTIONS (do not overfit here; broad is fine)
    evidence = run_retrieval(chief, demographics, top_n=cfg.top_n)

    if not evidence:
        return RagIntakeArtifacts(
            evidence=[],
            plan=build_generic_fallback_plan(),
            plan_mode="fallback",
            validation_reason="No evidence retrieved.",
        )

    try:
        draft = build_question_plan(
            chief=chief,
            demographics=demographics,
            evidence_chunks=evidence,
            llm_call_fn=llm_call_fn,
            max_questions=cfg.max_questions,
        )
    except IntakePlannerError as e:
        return RagIntakeArtifacts(
            evidence=evidence,
            plan=build_generic_fallback_plan(),
            plan_mode="fallback",
            validation_reason=f"Planner failed: {e}",
        )

    vr = validate_and_normalize_plan(draft, max_questions=cfg.max_questions)
    if not vr.ok or not vr.normalized_plan:
        return RagIntakeArtifacts(
            evidence=evidence,
            plan=build_generic_fallback_plan(),
            plan_mode="fallback",
            validation_reason=f"Plan validation failed: {vr.reason}",
        )

    return RagIntakeArtifacts(
        evidence=evidence,
        plan=vr.normalized_plan,
        plan_mode="rag",
        validation_reason=None,
    )


def compute_med_flags(
    structured_answers: Dict[str, Any],
    pregnancy_possible: str,
    allergies_text: str,
) -> MedExclusionFlags:
    med_excl_text = str(structured_answers.get("med_exclusions", "") or "")
    return parse_med_exclusions(
        med_exclusions_text=med_excl_text,
        pregnancy_possible=pregnancy_possible,
        allergies_text=allergies_text,
    )
