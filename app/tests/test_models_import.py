from app.core.models import CallState

if __name__ == "__main__":
    s = CallState(session_id="demo")
    print("OK:", s.model_dump())