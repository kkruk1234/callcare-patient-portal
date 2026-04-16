import os
from typing import List
from Bio import Entrez

# NCBI requires an email identifier
Entrez.email = os.environ.get("NCBI_EMAIL", "you@example.com")

def fetch_abstract_text(term: str, max_results: int = 25) -> str:
    search = Entrez.esearch(db="pubmed", term=term, retmax=max_results)
    srec = Entrez.read(search)
    ids = srec.get("IdList", [])
    if not ids:
        return ""

    fetch = Entrez.efetch(db="pubmed", id=",".join(ids), rettype="abstract", retmode="text")
    return fetch.read()

if __name__ == "__main__":
    q = "medication refill protocol primary care telemedicine"
    txt = fetch_abstract_text(q, max_results=10)
    print(txt[:2000])

