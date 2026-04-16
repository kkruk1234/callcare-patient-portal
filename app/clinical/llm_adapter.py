"""
CallCare LLM adapter

Goal: provide ONE function `llm_chat(system, user)` that works with:
- your existing app.clinical.llm_note_writer (if it exposes any usable function), OR
- OpenAI SDK (if installed + OPENAI_API_KEY present)

If neither is available, returns None (caller must fall back).
"""

from __future__ import annotations
from typing import Optional, Any, Dict
import os

def _try_call_existing_llm(system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 900) -> Optional[str]:
    """
    Try to reuse your existing LLM wiring in app.clinical.llm_note_writer without knowing its internals.
    """
    try:
        import importlib
        m = importlib.import_module("app.clinical.llm_note_writer")
    except Exception:
        return None

    # Common function names we try (in order)
    candidates = [
        "chat", "chat_complete", "complete_chat",
        "call_llm", "llm_chat", "ask_llm",
        "run_chat", "generate_text", "complete",
    ]

    for name in candidates:
        fn = getattr(m, name, None)
        if callable(fn):
            # We try several signature styles without crashing the app.
            for attempt in range(1, 6):
                try:
                    if attempt == 1:
                        out = fn(system=system, user=user, temperature=temperature, max_tokens=max_tokens)
                    elif attempt == 2:
                        out = fn(system, user)
                    elif attempt == 3:
                        out = fn([{"role":"system","content":system},{"role":"user","content":user}])
                    elif attempt == 4:
                        out = fn(prompt=f"{system}\n\n{user}")
                    else:
                        out = fn(user)
                except TypeError:
                    continue
                except Exception:
                    continue

                if isinstance(out, str) and out.strip():
                    return out.strip()
                # Sometimes returns dict-like
                if isinstance(out, dict):
                    txt = out.get("text") or out.get("content") or out.get("message")
                    if isinstance(txt, str) and txt.strip():
                        return txt.strip()
    return None


def _try_openai_sdk(system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 900) -> Optional[str]:
    """
    Optional fallback if OpenAI SDK is installed.
    Uses:
      - OPENAI_API_KEY
      - OPENAI_MODEL (optional; default: gpt-4o-mini)
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"

    # Try new SDK style first
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        txt = resp.choices[0].message.content
        return (txt or "").strip() or None
    except Exception:
        pass

    # Try legacy style
    try:
        import openai
        openai.api_key = api_key
        resp = openai.ChatCompletion.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        txt = resp["choices"][0]["message"]["content"]
        return (txt or "").strip() or None
    except Exception:
        return None


def llm_chat(system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 900) -> Optional[str]:
    """
    Main entry: returns assistant text, or None if LLM not available.
    """
    out = _try_call_existing_llm(system, user, temperature=temperature, max_tokens=max_tokens)
    if out:
        return out
    return _try_openai_sdk(system, user, temperature=temperature, max_tokens=max_tokens)
