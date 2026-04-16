from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class EvidenceChunk:
    key: str
    title: str
    url: str
    publisher: str
    date: Optional[str]
    text: str


@dataclass(frozen=True)
class Demographics:
    age_years: int
    pregnancy_possible: str
    allergies_text: str


class IntakePlannerError(Exception):
    pass


def _build_planner_prompt(chief: str, demo: Demographics, evidence: List[EvidenceChunk], max_questions: int) -> str:
    evid_lines = []
    for ch in evidence:
        meta = f"{ch.key} | {ch.publisher} | {ch.title} | {ch.url}"
        if ch.date:
            meta += f" | {ch.date}"
        evid_lines.append(meta + "\n" + ch.text.strip())

    evidence_block = "\n\n---\n\n".join(evid_lines)

    return f"""
You are generating a structured clinical INTAKE QUESTION PLAN for a telephone-only urgent care assistant.

You MUST output ONLY valid JSON. No markdown. No explanations.

Chief complaint: {chief}
Age: {demo.age_years}
Pregnancy possible: {demo.pregnancy_possible}
Allergies: {demo.allergies_text}

EVIDENCE:
{evidence_block}

Rules:
- Output JSON only
- Ask questions only (no diagnosis, no meds)
- Every question must cite evidence_keys
- Include med_exclusions free-text question if meds may be considered
- Max {max_questions} questions

Output schema:
{{
  "domain": string,
  "questions": [
    {{
      "id": string,
      "prompt": string,
      "type": "yesno" | "yesno_unknown" | "multiple_choice" | "free_text",
      "choices": [string],
      "store_as": string,
      "rationale": string,
      "evidence_keys": [string]
    }}
  ],
  "required_checks_included": {{
    "red_flags": boolean,
    "med_exclusions": boolean,
    "allergies": boolean,
    "pregnancy_possible": boolean
  }}
}}
""".strip()


def build_question_plan(
    chief: str,
    demographics: Demographics,
    evidence_chunks: List[EvidenceChunk],
    llm_call_fn,
    max_questions: int = 12,
) -> Dict[str, Any]:
    if not evidence_chunks:
        raise IntakePlannerError("No evidence provided")

    prompt = _build_planner_prompt(chief, demographics, evidence_chunks, max_questions)
    raw = llm_call_fn(prompt)

    try:
        plan = json.loads(raw)
    except Exception as e:
        raise IntakePlannerError(f"Invalid JSON from LLM: {e}")

    return plan
