# Deployment state, requests and monitoring.
DEPLOY_RUNS: dict = {}
DEPLOY_LOCK = threading.Lock()


DEPLOY_DONE = {"LIVE", "FAILED", "ROLLED_BACK", "DESTROYED"}


def _deploy_call(method: str, path: str, body=None, timeout=(2, None)):
    """One request to the deployment agent."""
    r = requests.request(method, f"http://127.0.0.1:{DEPLOY_PORT}{path}",
                         json=body, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        data = {"error": (r.text or "")[:400]}
    if r.status_code >= 400:
        raise RuntimeError(data.get("error") or f"HTTP {r.status_code}")
    return data


def _deploy_mongo_uri(settings: dict) -> str:
    """The database the deployed app will use."""
    explicit = str(settings.get("deploy_mongodb_uri", "")).strip()
    if explicit:
        return explicit
    fallback = str(settings.get("mongodb_uri", "")).strip()
    if fallback and not re.search(r"(?:@|//)(?:127\.0\.0\.1|localhost|\[::1\])",
                                  fallback):
        return fallback
    return ""


def deploy_settings_summary() -> dict:
    """What is configured for deploying, and nothing that could be replayed."""
    s = load_settings()
    token = str(s.get("vercel_token", "")).strip()
    mongo = _deploy_mongo_uri(s)

    cli_token = False
    try:
        from deployment_agent.vercel_auth import _candidate_auth_files
        cli_token = any(p.is_file() for p in _candidate_auth_files())
    except Exception:                                           # noqa: BLE001
        pass
    return {
        "vercel_cli_signed_in": cli_token,
        "aws_profile": str(s.get("aws_profile", "")).strip(),
        "aws_region": str(s.get("aws_region", "")).strip(),
        "aws_start_url": str(s.get("aws_start_url", "")).strip(),
        "aws_sso_region": str(s.get("aws_sso_region", "")).strip(),
        "vercel_token_set": bool(token),
        "vercel_token_hint": (f"…{token[-4:]}" if token else ""),
        "mongodb_uri_set": bool(mongo),
        "mongodb_uri_hint": _redact_uri(mongo),
        "deploy_model": str(s.get("deploy_model", "")).strip(),
    }


def start_deployment(project: str, target: str, opts: dict) -> dict:
    """Begin a deployment and answer immediately."""
    if not project:
        raise ValueError("project is required")
    proj_dir = PROD_DIR / project
    if not proj_dir.is_dir():
        raise ValueError(f"no such project: {project}")
    if target not in ("vercel", "aws_ec2", "aws_ecs"):
        raise ValueError(f"unsupported target: {target}")
    if DEPLOY_API["state"] in ("off", "import-failed"):
        raise ValueError("the deployment agent is not running")

    settings = load_settings()
    mongo = str(opts.get("mongodb_uri", "")).strip() or _deploy_mongo_uri(settings)
    if not mongo:
        raise ValueError("no production MongoDB URI — set one in Settings")

    if target.startswith("aws_") and not str(settings.get("aws_profile", "")).strip():
        raise ValueError("no AWS account connected — sign in from Settings")

    with DEPLOY_LOCK:
        current = DEPLOY_RUNS.get(project)
        if current and current.get("state") not in DEPLOY_DONE and not current.get("error"):
            raise ValueError("a deployment for this project is already running")
        DEPLOY_RUNS[project] = {
            "project": project, "target": target, "run_id": "",
            "state": "STARTING", "phase": "analysis", "percent": 0,
            "message": "Starting…", "error": "", "url": "",
            "started": time.time(), "finished": None, "events": [],

            "cursor": 0,
        }

    threading.Thread(target=_deploy_autopilot,
                     args=(project, target, dict(opts), mongo, settings),
                     daemon=True).start()
    return dict(DEPLOY_RUNS[project])


def _deploy_set(project: str, **patch):
    run = DEPLOY_RUNS.get(project)
    if run is not None:
        run.update(patch)


def _deploy_drain_events(project: str, run_id: str, after: int) -> int:
    """Pull whatever the agent has emitted since `after` into AgentForge's log."""
    try:
        data = _deploy_call("GET", f"/api/runs/{run_id}/events?after={after}",
                            timeout=(2, 30))
    except Exception:
        return after
    run = DEPLOY_RUNS.get(project)
    for ev in data.get("events") or []:
        after = max(after, int(ev.get("event_id") or 0))
        msg = str(ev.get("message") or "").strip()
        if not msg:
            continue
        kind = str(ev.get("type") or "")
        level = {"error": "ERROR", "warning": "WARN"}.get(kind, "INFO")
        elog(level, f"   🚀 {msg}")
        if run is not None:
            run["events"] = (run["events"] + [{
                "id": int(ev.get("event_id") or 0),
                "type": kind, "stage": ev.get("stage") or "",
                "status": ev.get("status") or "",
                "percent": int(ev.get("percent") or 0), "message": msg,
            }])[-400:]
            if kind != "log":
                run["phase"] = ev.get("stage") or run["phase"]
                run["percent"] = int(ev.get("percent") or run["percent"])
                run["message"] = msg
    return after


def _deploy_serving(run: dict) -> bool:
    """Is the thing actually answering?"""
    url = str((run.get("repo") or {}).get("application_url") or "").strip()
    if not url:
        return False
    for path in ("/api/health", "/"):
        try:
            r = requests.get(url.rstrip("/") + path, timeout=(5, 20),
                             allow_redirects=False)
            if r.status_code < 400:
                return True
        except requests.RequestException:
            continue
    return False


def _deploy_wait(project: str, run_id: str, until: set, deadline: float,
                 settle_from: set = frozenset(), settle_after: float = 240.0) -> dict:
    """Poll the run until it reaches one of `until`, draining events as it goes."""
    parked_since = None
    while time.time() < deadline:
        run_state = DEPLOY_RUNS.get(project) or {}
        cursor = _deploy_drain_events(project, run_id,
                                      int(run_state.get("cursor") or 0))
        _deploy_set(project, cursor=cursor)
        try:
            run = _deploy_call("GET", f"/api/runs/{run_id}", timeout=(2, 30))
        except Exception as e:
            elog("WARN", f"   ⚠️  could not read the deployment: {e}")
            time.sleep(4)
            continue
        state = str(run.get("state") or "")
        _deploy_set(project, state=state, run_id=run_id)
        if state in until:
            _deploy_set(project,
                        cursor=_deploy_drain_events(project, run_id, cursor))
            return run
        if state in settle_from:
            parked_since = parked_since or time.time()
            if time.time() - parked_since > settle_after and _deploy_serving(run):
                elog("INFO", "   🚀 the site is answering; not waiting for the "
                             "agent's readiness score")
                _deploy_set(project, cursor=_deploy_drain_events(project, run_id, cursor))
                return {**run, "state": "LIVE"}
        else:
            parked_since = None
        time.sleep(2.5)

    try:
        run = _deploy_call("GET", f"/api/runs/{run_id}", timeout=(2, 30))
        if _deploy_serving(run):
            return {**run, "state": "LIVE"}
    except Exception:                                           # noqa: BLE001
        pass
    raise TimeoutError("the deployment agent stopped responding")


def _deploy_autopilot(project: str, target: str, opts: dict,
                      mongo: str, settings: dict):
    """Analyze → wait for review → deploy → wait for terminal → adopt."""
    proj_dir = PROD_DIR / project
    try:
        elog("INFO", f"🚀 Deploying {project} to "
                     f"{'Vercel' if target == 'vercel' else 'AWS EC2'}")
        started = _deploy_call("POST", "/api/runs/analyze", {
            "cloud_consent": True,
            "path": str(proj_dir),
            "target": target,
            "validate_container": bool(opts.get("validate_container", True)),
        })
        run_id = str(started.get("run_id") or "")
        _deploy_set(project, run_id=run_id, state="ANALYZING",
                    message="Reading the project…")
        elog("INFO", f"   🚀 run {run_id[:8]} — analysing")

        run = _deploy_wait(project, run_id, {"REVIEW_READY", "FAILED"},
                           time.time() + 3600)
        if run.get("state") != "REVIEW_READY":
            raise RuntimeError(run.get("error")
                               or "analysis did not reach a reviewable state")

        plan = run.get("plan") or {}
        if not plan.get("model_used"):
            raise RuntimeError(
                "the deployment plan was written without a model, and the agent "
                "will not deploy one — check the deploy model in Settings")

        body = {"approved": True, "mongodb_uri": mongo}
        if target == "vercel":
            token = str(settings.get("vercel_token", "")).strip()
            if token:
                body["vercel_token"] = token
        else:

            body["aws_profile"] = str(settings.get("aws_profile", "")).strip()
            body["region"] = (str(settings.get("aws_region", "")).strip()
                              or "ap-south-1")

        _deploy_set(project, state="DEPLOYING", message="Deploying…")
        elog("INFO", "   🚀 review passed — deploying")
        _deploy_call("POST", f"/api/runs/{run_id}/deploy", body)

        run = _deploy_wait(project, run_id, DEPLOY_DONE, time.time() + 5400,
                           settle_from={"VALIDATING"}, settle_after=240)

        url = str((run.get("repo") or {}).get("application_url") or "")
        _deploy_set(project, url=url, finished=time.time())
        if run.get("state") == "LIVE":
            elog("SUCCESS", f"   🚀 live{f' at {url}' if url else ''}")
        else:
            _deploy_set(project, error=str(run.get("error") or "")
                        or f"deployment ended {run.get('state')}")
            elog("ERROR", f"   🚀 deployment ended {run.get('state')}"
                          f" — {run.get('error') or 'no reason given'}")
        adopt_deploy(run_id, proj_dir)
    except Exception as e:
        _deploy_set(project, error=f"{type(e).__name__}: {e}",
                    state="FAILED", finished=time.time())
        elog("ERROR", f"   🚀 deployment failed — {type(e).__name__}: {e}")


def adopt_deploy(run_id: str, proj_dir: Path) -> bool:
    """Put the deployment's record into the project it deployed."""
    from datetime import datetime, timezone
    try:
        dest = proj_dir / ".agentforge" / "deploy"
        dest.mkdir(parents=True, exist_ok=True)

        run = _deploy_call("GET", f"/api/runs/{run_id}", timeout=(2, 60))
        (dest / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")

        events = _deploy_call("GET", f"/api/runs/{run_id}/events?after=0",
                              timeout=(2, 60))
        (dest / "events.json").write_text(json.dumps(events, indent=2),
                                          encoding="utf-8")

        try:
            snap = _deploy_call("GET", f"/api/runs/{run_id}/monitor",
                                timeout=(2, 120))
            (dest / "monitor.json").write_text(json.dumps(snap, indent=2),
                                               encoding="utf-8")
        except Exception:
            pass

        (dest / "link.json").write_text(json.dumps({
            "run_id": run_id,
            "adopted_at": datetime.now(timezone.utc).isoformat(),
            "state": run.get("state", ""),
            "target": (run.get("plan") or {}).get("target", ""),
        }, indent=2), encoding="utf-8")

        elog("INFO", f"   🚀 deployment record saved ({run.get('state')})")
        return True
    except Exception as e:
        elog("WARN", f"   ⚠️  Could not save the deployment record: "
                     f"{type(e).__name__}: {e}")
        return False


def retire_deploy(proj_dir: Path, run_id: str) -> bool:
    """Move a finished deployment's record out of the way, keeping every byte."""
    from datetime import datetime, timezone
    try:
        live = proj_dir / ".agentforge" / "deploy"
        if not live.is_dir():
            return False
        archive = proj_dir / ".agentforge" / "deploy-archive" / (run_id or "unknown")
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():

            archive = archive.with_name(f"{archive.name}-{int(time.time())}")
        shutil.move(str(live), str(archive))

        (proj_dir / ".agentforge" / "deploy-deleted.json").write_text(
            json.dumps({"run_id": run_id,
                        "deleted_at": datetime.now(timezone.utc).isoformat(),
                        "archive": archive.name}, indent=2),
            encoding="utf-8")
        log.info(f"Retired deployment record to {archive.name}")
        return True
    except Exception as e:

        log.warning(f"Could not retire deployment record: {type(e).__name__}: {e}")
        return False


def _deploy_run_state(run_id: str, fallback: str) -> str:
    """The run's state according to the agent, not according to a stale file."""
    if not run_id:
        return fallback
    try:
        fresh = _deploy_call("GET", f"/api/runs/{run_id}", timeout=(2, 5))
        return str(fresh.get("state") or fallback)
    except Exception:
        return fallback


def read_deploy_results(proj_name: str) -> dict:
    """Everything the Deploy tab shows, gathered server-side."""
    proj_dir = PROD_DIR / proj_name
    if not proj_dir.is_dir():
        return {"error": f"no such project: {proj_name}"}

    def load(path, default=None):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    d_dir = proj_dir / ".agentforge" / "deploy"
    live = DEPLOY_RUNS.get(proj_name)
    out = {
        "project": proj_name,
        "agent": deploy_status(),
        "live": dict(live) if live else None,
        "have": {"last": False},
        "settings": deploy_settings_summary(),
    }
    if d_dir.is_dir():
        run = load(d_dir / "run.json", {}) or {}

        if run and _deploy_run_state(run.get("id", ""),
                                     run.get("state", "")) == "DESTROYED":
            retire_deploy(proj_dir, run.get("id", ""))
            run = {}
        if run:
            out["have"]["last"] = True
            out["last"] = {
                "run_id": run.get("id", ""),
                "state": run.get("state", ""),
                "target": (run.get("plan") or {}).get("target", ""),
                "readiness": run.get("readiness") or {},

                "repo_state": run.get("repo") or {},
                "error": run.get("error") or "",
                "link": load(d_dir / "link.json", {}) or {},

                "monitor": load(d_dir / "monitor.json", {}) or {},
                "events_count": len(load(d_dir / "events.json", {}).get("events", [])
                                    if isinstance(load(d_dir / "events.json", {}), dict)
                                    else load(d_dir / "events.json", []) or []),
            }

    marker = load(proj_dir / ".agentforge" / "deploy-deleted.json", {}) or {}
    if marker and not out["have"]["last"]:
        out["deleted"] = marker
    return out
