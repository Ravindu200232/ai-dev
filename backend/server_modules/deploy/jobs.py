# Background deployment jobs.
_DEPLOY_JOBS: dict = {}
_DEPLOY_JOBS_LOCK = threading.Lock()
_DEPLOY_KEEP_FINISHED_S = 900
_DEPLOY_MAX_JOBS = 200


def _deploy_reap():
    now = time.time()
    done = [(j["finished"], jid) for jid, j in _DEPLOY_JOBS.items() if j.get("finished")]
    for finished, jid in done:
        if now - finished > _DEPLOY_KEEP_FINISHED_S:
            _DEPLOY_JOBS.pop(jid, None)
    if len(_DEPLOY_JOBS) > _DEPLOY_MAX_JOBS:
        for _, jid in sorted(done)[:len(_DEPLOY_JOBS) - _DEPLOY_MAX_JOBS]:
            _DEPLOY_JOBS.pop(jid, None)


def _deploy_job_run(job_id: str, method: str, path: str, body):
    job = _DEPLOY_JOBS[job_id]
    try:
        r = requests.request(method, f"http://127.0.0.1:{DEPLOY_PORT}{path}",
                             json=body if method != "GET" else None,

                             timeout=(2, None))
        job["http_status"] = r.status_code
        try:
            job["result"] = r.json()
        except Exception:
            job["result"] = {"text": r.text}

        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["finished"] = time.time()


def deploy_job_start(method: str, path: str, body) -> dict:
    """Begin the work and answer immediately."""
    if not path.startswith("/"):
        raise ValueError("path must be absolute")

    if path.startswith("/jobs"):
        raise ValueError("jobs cannot run jobs")
    with _DEPLOY_JOBS_LOCK:
        _deploy_reap()
        job_id = "djob_" + uuid.uuid4().hex[:20]
        _DEPLOY_JOBS[job_id] = {"status": "running", "path": path,
                                "started": time.time(), "finished": None,
                                "http_status": None, "result": None, "error": ""}
    threading.Thread(target=_deploy_job_run,
                     args=(job_id, method, path, body), daemon=True).start()
    return {"job_id": job_id, "status": "running", "path": path}


def deploy_job_poll(job_id: str) -> dict:
    job = _DEPLOY_JOBS.get(job_id)
    if job is None:

        raise KeyError("no such job (it may have expired)")
    elapsed = (job.get("finished") or time.time()) - job["started"]
    return {"job_id": job_id, **job, "elapsed": round(elapsed, 1)}
