import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List
import yaml

PATHWAY_DIR = Path("app/clinical/pathways")
PACK_DIR = Path("logs/question_packs")

SYSTEM = """You rewrite canonical clinical questions into natural, concise telephone dialogue.

Hard rules:
- You are ONLY rewriting wording. Do NOT add or remove clinical content.
- Ask exactly one question for each item.
- Keep it short and conversational.
- Preserve answer choices if present.
- Do NOT diagnose, prescribe, or give dosing.
- Output must be valid JSON ONLY.

Return JSON in this exact format:
{
  "rewrites": {
    "<question_id>": "<natural_question>",
    ...
  }
}
"""

def load_pathway_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def openai_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def call_with_backoff(payload: str, max_retries: int = 8) -> str:
    """
    Calls the model. If rate limited (429), waits and retries automatically.
    """
    client = openai_client()
    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.responses.create(
                model="gpt-4.1-mini",
                input=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": payload},
                ],
                temperature=0.3,
            )
            return (resp.output_text or "").strip()
        except Exception as e:
            msg = str(e)
            # Detect common rate limit signal
            if "RateLimitError" in msg or "rate_limit" in msg or "429" in msg:
                # Wait, then retry. Increase delay each time.
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            # Other errors: re-raise
            raise
    raise RuntimeError("Too many rate limit retries; try again later or add billing to raise limits.")

def build_pack_for_pathway(pathway_file: Path) -> None:
    data = load_pathway_yaml(pathway_file)
    pid = data["id"]
    version = str(data.get("version", "0.0"))
    questions: List[Dict[str, Any]] = data.get("questions", [])

    PACK_DIR.mkdir(parents=True, exist_ok=True)
    pack_path = PACK_DIR / f"{pid}.json"

    # Load existing pack for resume
    pack: Dict[str, Any] = {"id": pid, "version": version, "questions": {}}
    if pack_path.exists():
        try:
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
        except Exception:
            pack = {"id": pid, "version": version, "questions": {}}

    # If version changes, keep old but we’ll overwrite missing/new ones
    pack["id"] = pid
    pack["version"] = version
    pack.setdefault("questions", {})

    # Only rewrite missing question IDs
    missing: Dict[str, str] = {}
    for q in questions:
        qid = q.get("id", "").strip()
        canonical = (q.get("prompt", "") or "").strip()
        if not qid or not canonical:
            continue
        if qid in pack["questions"]:
            continue
        missing[qid] = canonical

    if not missing:
        print(f"Already complete: {pid}")
        return

    # Batch rewrite ALL missing questions in ONE request
    payload = json.dumps({"pathway_id": pid, "version": version, "canonical_questions": missing}, indent=2)
    raw = call_with_backoff(payload)

    try:
        parsed = json.loads(raw)
        rewrites = parsed.get("rewrites", {})
        if not isinstance(rewrites, dict):
            raise ValueError("Bad rewrites shape")

        # Save rewrites; fall back to canonical if something missing
        for qid, canonical in missing.items():
            natural = str(rewrites.get(qid, "")).strip()
            pack["questions"][qid] = natural if natural else canonical

        pack_path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
        print(f"Done: {pack_path} (added {len(missing)} questions)")

    except Exception:
        # If parsing fails, still save canonical to avoid blocking progress
        for qid, canonical in missing.items():
            pack["questions"][qid] = canonical
        pack_path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
        print(f"WARNING: Could not parse model output for {pid}. Saved canonical prompts instead.")
        print("You can re-run later to regenerate natural phrasing for this pathway.")

def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Run: export OPENAI_API_KEY='...'\n")

    files = sorted(PATHWAY_DIR.glob("*.yaml"))
    if not files:
        raise SystemExit("No pathway YAML files found in app/clinical/pathways\n")

    for f in files:
        build_pack_for_pathway(f)

if __name__ == "__main__":
    main()
