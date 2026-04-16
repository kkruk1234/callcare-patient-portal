"""
CallCare TEXT CALL SIMULATOR (NO-FAISS)

Run:
  cd ~/Desktop/callcare
  python3 -m app.tests.simulate_text_call_no_faiss
"""

import os

# Force: do NOT use FAISS retrieval during sim
os.environ["CALLCARE_DISABLE_FAISS"] = "1"

# Default evidence model if not already set externally
os.environ.setdefault("CALLCARE_LLM_EVIDENCE_MODEL", "gpt-4o-mini")

# Now run the normal simulator
from app.tests.simulate_text_call import run

if __name__ == "__main__":
    run()
