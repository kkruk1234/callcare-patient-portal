from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from app.core.models import CallState, NoteDraft, ReviewPacket
from app.clinical.note_builder import now_iso
from app.clinical.med_protocols import load_protocol_text


QUEUE_PATH = Path("logs/review_queue.jsonl")
QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _safe_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x if i is not None and str(i).strip()]
    return []


# __CC_REVIEWQ_EVID_COERCE_V1
# Pydantic (ReviewPacket) expects EvidenceRef objects from app.core.models.
# note.evidence may contain dicts OR EvidenceRef-like objects from other modules.
# Convert everything to plain dicts so Pydantic can build the correct EvidenceRef type.
def _evidence_item_to_dict(x: Any) -> Dict[str, str]:
    if x is None:
        return {"title": "", "source": "", "url": "", "snippet": ""}

    if isinstance(x, dict):
        d = dict(x)
    elif hasattr(x, "model_dump"):
        try:
            d = x.model_dump()
        except Exception:
            d = dict(getattr(x, "__dict__", {}) or {})
    else:
        d = dict(getattr(x, "__dict__", {}) or {})

    title = (d.get("title") or getattr(x, "title", "") or "").strip()

    # Different parts of the codebase sometimes store the URL under "url" or "source".
    url = (d.get("url") or d.get("source") or getattr(x, "url", "") or getattr(x, "source", "") or "").strip()

    # Some evidence objects carry "publisher"; ReviewPacket EvidenceRef wants "source"
    publisher = (d.get("publisher") or getattr(x, "publisher", "") or "").strip()
    source = (d.get("source") or publisher or url or "").strip()

    snippet = d.get("snippet")
    if snippet is None:
        snippet = ""
    snippet = str(snippet)

    # Only include fields ReviewPacket EvidenceRef is guaranteed to accept
    return {"title": title, "source": source, "url": url, "snippet": snippet}


def _coerce_evidence_list(ev: Any) -> List[Dict[str, str]]:
    if not ev:
        return []
    if not isinstance(ev, list):
        return []
    out: List[Dict[str, str]] = []
    for item in ev:
        if item is None:
            continue
        out.append(_evidence_item_to_dict(item))
    return out


def enqueue_for_review(state: CallState, note: NoteDraft) -> ReviewPacket:
    """
    Create a ReviewPacket for async clinician oversight and append it to a local JSONL queue.
    This is the Tier-1 "physician asynchronous approval" mechanism.

    We also attach deterministic disposition signals produced by the state machine:
      - state.symptoms["_disposition"]
      - state.symptoms["_disposition_reasons"]

    Because ReviewPacket is intentionally minimal, we store these under reserved keys in answers:
      - "__disposition"
      - "__disposition_reasons"
    """
    pathway = (state.symptoms.get("_pathway") or {})
    answers: Dict[str, Any] = dict(state.symptoms.get("_answers") or {})

    # Attach deterministic disposition (if present)
    disposition = state.symptoms.get("_disposition")
    reasons = _safe_list(state.symptoms.get("_disposition_reasons"))

    if disposition:
        answers["__disposition"] = str(disposition)
    if reasons:
        answers["__disposition_reasons"] = reasons

    note_evidence = _coerce_evidence_list(getattr(note, "evidence", None))

    packet = ReviewPacket(
        packet_id=str(uuid.uuid4()),
        created_at=now_iso(),
        session_id=str(state.session_id),
        chief_complaint=state.chief_complaint or "",
        age_band=state.age_band or "",
        pregnancy_possible=str(state.symptoms.get("pregnancy_possible", state.pregnancy_possible or "")),
        pathway_id=str(pathway.get("id") or ""),
        answers=answers,
        note=note,
        evidence=note_evidence,
        med_suggestions=[
            {
                "pathway_id": str(pathway.get("id") or ""),
                "protocol_text": load_protocol_text(str(pathway.get("id") or "").strip() or "cough_uri"),
                "drug_allergies": state.symptoms.get("drug_allergies", ""),
                "weight": state.symptoms.get("weight", ""),
                "pregnancy_possible": state.symptoms.get("pregnancy_possible", state.pregnancy_possible or ""),
            }
        ],
    )

    # Append to queue (JSONL is easy to inspect + audit)
    QUEUE_PATH.write_text("", encoding="utf-8") if not QUEUE_PATH.exists() else None
    with QUEUE_PATH.open("a", encoding="utf-8") as f:
        f.write(packet.model_dump_json() + "\n")

    return packet
