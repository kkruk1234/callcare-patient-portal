from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml

PATHWAY_DIR = Path("app/clinical/pathways")


@dataclass
class Pathway:
    id: str
    version: str
    match_any: List[str]
    questions: List[Dict[str, Any]]
    stop_rules: List[Dict[str, Any]]
    routing: Dict[str, Any]


def load_pathways() -> List[Pathway]:
    out: List[Pathway] = []
    for p in sorted(PATHWAY_DIR.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        out.append(
            Pathway(
                id=data["id"],
                version=str(data.get("version", "0.0")),
                match_any=[s.lower() for s in data.get("match", {}).get("any_keywords", [])],
                questions=data.get("questions", []),
                stop_rules=data.get("stop_rules", []),
                routing=data.get("routing", {}),
            )
        )
    return out


def choose_pathway(chief_complaint: str, pathways: List[Pathway]) -> Pathway:
    """
    Choose pathway by keyword substring match.
    IMPORTANT: If nothing matches, do NOT pick the first file.
    Fall back to cough_uri (URI bucket) if available.
    """
    cc = (chief_complaint or "").lower().strip()

    best: Optional[Pathway] = None
    best_score = 0  # require a real match (>0)

    for pw in pathways:
        score = 0
        for kw in pw.match_any:
            kw = (kw or "").strip().lower()
            if not kw:
                continue
            if kw in cc:
                score = max(score, len(kw))
        if score > best_score:
            best = pw
            best_score = score

    if best is not None:
        return best

    # Fallback: cough/URI bucket if present
    for pw in pathways:
        if pw.id == "cough_uri":
            return pw

    # Otherwise just return the first available pathway (should be rare)
    return pathways[0]


def next_pathway_question(pw: Pathway, answers: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for q in pw.questions:
        qid = q["id"]
        if qid not in answers:
            return q
    return None


def should_stop(pw: Pathway, answers: Dict[str, Any]) -> bool:
    for rule in pw.stop_rules:
        needed = rule.get("if_all_answered", [])
        if all(k in answers for k in needed):
            return True
    return False
