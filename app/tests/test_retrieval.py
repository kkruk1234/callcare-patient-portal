from app.rag.retrieve import retrieve

if __name__ == "__main__":
    results = retrieve("adult sore throat runny nose cough less than 10 days", k=3)
    for r in results:
        print("\n---")
        print("SCORE:", r["score"])
        print("SOURCE:", r["source"])
        print(r["text"][:400])
