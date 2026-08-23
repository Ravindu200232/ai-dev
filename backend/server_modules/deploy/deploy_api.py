# Runs and reports the deployment sidecar API.

def start_deploy_api():
    """Run the deployment agent's HTTP server."""
    try:
        sys.path.insert(0, str(BASE_DIR / "deployment-agent"))
        from deploy_agent import mount
    except Exception as e:
        DEPLOY_API.update(state="import-failed", error=f"{type(e).__name__}: {e}")
        print(f"⚠️  Deployment agent unavailable — {DEPLOY_API['error']}")
        return

    DEPLOY_API.update(state="starting", error="")
    try:
        mount.serve(port=DEPLOY_PORT)
        DEPLOY_API.update(state="stopped")
    except Exception as e:
        DEPLOY_API.update(state="crashed", error=f"{type(e).__name__}: {e}")
        print(f"⚠️  Deployment agent stopped — {DEPLOY_API['error']}")


def deploy_status() -> dict:
    """What the thread believes, plus whether the port is measurably there."""
    import socket
    listening = False
    try:
        with socket.create_connection(("127.0.0.1", DEPLOY_PORT), timeout=0.5):
            listening = True
    except OSError:
        pass
    return {**DEPLOY_API, "listening": listening}
