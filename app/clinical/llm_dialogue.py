import os
import json
import random
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

CACHE_PATH = Path("logs/dialogue_cache.json")

SYSTEM = """You are the voice of an automated clinical assistant on a telephone call.
You do NOT decide what clinical question to ask; you only phrase it naturally.

Hard rules:
- Ask EXACTLY ONE question per turn.
- Do NOT add extra clinical questions beyond the CANONICAL_QUESTION.
- Do NOT diagnose.
- Do NOT prescribe or give dosing.
- Keep it short, clear, and conversational for a phone call.
- Preserve answer choices if present.

Acknowledgement rules:
- The acknowledgement MUST NOT repeat, paraphrase, or quote the user's answer.
- Generic and brief (max 6 words).

Return JSON only with keys:
- question: string
- ack: string
"""

FALLBACK_ACKS = [
    "Got it.",
    "Okay.",
    "Thanks.",
    "Understood.",
    "All right.",
]

def _load_cache() -> Dict[str, Dict[str, str]]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_cache(cache: Dict[str, Dict[str, str]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")

def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def get_dialogue(canonical_question: str, context: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    context = context or {}
    key = canonical_question.strip()

    cache = _load_cache()
    if key in cache and "question" in cache[key] and "ack" in cache[key]:
        return cache[key]["question"], cache[key]["ack"]

    fallback_q = canonical_question.strip()
    fallback_ack = random.choice(FALLBACK_ACKS)

    payload = {
        "canonical_question": canonical_question,
        "context_for_tone_only": context,
    }

    try:
        client = _client()
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.4,
        )
        text = (resp.output_text or "").strip()
        data = json.loads(text)

        natural = str(data.get("question", fallback_q)).strip() or fallback_q
        ack = str(data.get("ack", fallback_ack)).strip() or fallback_ack

        # Safety clamps
        if len(ack.split()) > 6:
            ack = fallback_ack
        if "{" in ack or "}" in ack:
            ack = fallback_ack

        cache[key] = {"question": natural, "ack": ack}
        _save_cache(cache)
        return natural, ack

    except Exception:
        return fallback_q, fallback_ack
