from app.rag.llm_http_evidence import llm_http_evidence, format_evidence_lines

evs = llm_http_evidence(
    chief_complaint="back pain",
    diagnosis_hint="musculoskeletal back pain",
    context="musculoskeletal back pain",
    min_urls=4,
    max_urls=6,
    debug=True,
)

print("\nRAW REFS:", len(evs))
for e in evs:
    print("-", e)

print("\nFORMATTED:")
for line in format_evidence_lines(evs):
    print(line)
