# Small background jobs used by HTTP endpoints.
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()
_JOBS_KEEP_FINISHED_S = 900
_JOBS_MAX = 200


_JOB_PATHS = ("/image", "/logo-prompt", "/themes", "/tune")


def _jobs_reap():
    now = time.time()
    done = [(j["finished"], jid) for jid, j in _JOBS.items() if j.get("finished")]
    for finished, jid in done:
        if now - finished > _JOBS_KEEP_FINISHED_S:
            _JOBS.pop(jid, None)
    if len(_JOBS) > _JOBS_MAX:
        for _, jid in sorted(done)[:len(_JOBS) - _JOBS_MAX]:
            _JOBS.pop(jid, None)


def _job_run(job_id: str, method: str, path: str, body):
    job = _JOBS[job_id]
    try:
        r = requests.request(
            method, f"http://127.0.0.1:{UI_PORT}{AGENTFORGE_PREFIX}/api{path}",
            json=body if method != "GET" else None,

            timeout=(5, None))
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


def job_start(method: str, path: str, body) -> dict:
    """Begin the work and answer immediately."""
    path = str(path or "")
    if not path.startswith("/"):
        raise ValueError("path must be absolute")
    if path.split("?")[0] not in _JOB_PATHS:
        raise ValueError(f"{path} cannot be run as a job")
    with _JOBS_LOCK:
        _jobs_reap()
        job_id = "job_" + uuid.uuid4().hex[:20]
        _JOBS[job_id] = {"status": "running", "path": path,
                         "started": time.time(), "finished": None,
                         "http_status": None, "result": None, "error": ""}
    threading.Thread(target=_job_run, args=(job_id, method, path, body),
                     daemon=True).start()
    return {"job_id": job_id, "status": "running", "path": path}


def job_poll(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None:

        return {"job_id": job_id, "status": "unknown",
                "error": "no such job — it may have expired"}
    elapsed = (job.get("finished") or time.time()) - job["started"]
    return {"job_id": job_id, **job, "elapsed": round(elapsed, 1)}
