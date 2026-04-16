import json
from pathlib import Path
from typing import Optional

PACK_DIR = Path("logs/question_packs")

def get_natural_question(pathway_id: str, pathway_version: str, question_id: str, canonical: str) -> str:
    """
    Returns natural question from the saved question pack.
    Falls back to canonical if missing or version mismatch.
    """
    pack_path = PACK_DIR / f"{pathway_id}.json"
    if not pack_path.exists():
        return canonical

    try:
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
    except Exception:
        return canonical

    if str(pack.get("version", "")) != str(pathway_version):
        # Version changed; pack may be stale. Use canonical until rebuilt.
        return canonical

    qmap = pack.get("questions", {})
    return str(qmap.get(question_id, canonical)).strip() or canonical
