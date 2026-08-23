# Runs and reports the SRS sidecar API.
SRS_API = {"state": "off", "port": SRS_PORT, "error": ""}


def start_srs_api():
    """Run the SRS agent's FastAPI app."""
    try:
        from srs_agent import mount
    except Exception as e:
        SRS_API.update(state="import-failed", error=f"{type(e).__name__}: {e}")
        print(f"⚠️  SRS agent unavailable — {SRS_API['error']}")
        return

    SRS_API.update(state="starting", error="")
    try:
        mount.serve(port=SRS_PORT)
        SRS_API.update(state="stopped")
    except Exception as e:
        SRS_API.update(state="crashed", error=f"{type(e).__name__}: {e}")
        print(f"⚠️  SRS agent stopped — {SRS_API['error']}")


def srs_status() -> dict:
    """Is the SRS agent actually there?"""
    import socket
    listening = False
    try:
        with socket.create_connection(("127.0.0.1", SRS_PORT), timeout=0.5):
            listening = True
    except OSError:
        pass
    return {**SRS_API, "listening": listening}


DEPLOY_API = {"state": "off", "port": DEPLOY_PORT, "error": ""}
