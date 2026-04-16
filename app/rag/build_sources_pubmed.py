import os
from pathlib import Path
from app.rag.fetch_pubmed import fetch_abstract_text

OUT = Path("data/sources")
OUT.mkdir(parents=True, exist_ok=True)

def save(term: str, filename: str, n: int = 25):
    txt = fetch_abstract_text(term, max_results=n)
    if not txt.strip():
        raise SystemExit(f"No PubMed results for: {term}")
    (OUT / filename).write_text(txt, encoding="utf-8")
    print("Wrote:", OUT / filename)

if __name__ == "__main__":
    save("telemedicine medication refill protocol outcomes", "pubmed_refill_telemedicine.txt", n=25)
    save("telephone triage primary care outcomes", "pubmed_telephone_care.txt", n=25)
    save("asynchronous physician review clinical decision support safety", "pubmed_async_review_safety.txt", n=25)

