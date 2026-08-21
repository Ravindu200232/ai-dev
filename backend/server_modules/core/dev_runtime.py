# Development server lifecycle and shared runtime I/O.
def _route_of(payload: dict) -> str:
    """The page a picker-driven edit was made on, for the preview reload."""
    route = (payload or {}).get("route") or "/"
    route = str(route).strip()

    return route if route.startswith("/") and "//" not in route else "/"
def eerr(txt):

    log.error(txt)
    emit({"type": "error", "text": txt})


_STREAM_SEND_LOCK = threading.Lock()
_STREAM_SEND_FILE = ""
_STREAM_SEND_TEXT = ""
_STREAM_SEND_TIMER = None
_STREAM_SEND_DELAY = 0.03
_STREAM_SEND_MAX = 8192


def _take_stream_payload_locked():
    global _STREAM_SEND_FILE, _STREAM_SEND_TEXT, _STREAM_SEND_TIMER
    if not _STREAM_SEND_TEXT:
        _STREAM_SEND_TIMER = None
        return None
    payload = {"type": "stream", "file": _STREAM_SEND_FILE, "token": _STREAM_SEND_TEXT}
    _STREAM_SEND_FILE = ""
    _STREAM_SEND_TEXT = ""
    _STREAM_SEND_TIMER = None
    return payload


def _flush_stream_send():
    with _STREAM_SEND_LOCK:
        payload = _take_stream_payload_locked()
    if payload:
        emit(payload)


def _cancel_stream_timer_locked():
    global _STREAM_SEND_TIMER
    timer = _STREAM_SEND_TIMER
    _STREAM_SEND_TIMER = None
    if timer:
        timer.cancel()


def estream_start(fname):
    _flush_stream_send()
    emit({"type": "stream_start", "file": fname})


def estream(fname, tok):
    global _STREAM_SEND_FILE, _STREAM_SEND_TEXT, _STREAM_SEND_TIMER
    token = str(tok or "")
    if not token:
        return

    pending = None
    send_now = None
    timer_to_start = None
    with _STREAM_SEND_LOCK:
        if _STREAM_SEND_TEXT and _STREAM_SEND_FILE != fname:
            _cancel_stream_timer_locked()
            pending = _take_stream_payload_locked()

        _STREAM_SEND_FILE = fname
        _STREAM_SEND_TEXT += token
        if len(_STREAM_SEND_TEXT) >= _STREAM_SEND_MAX:
            _cancel_stream_timer_locked()
            send_now = _take_stream_payload_locked()
        elif _STREAM_SEND_TIMER is None:
            timer_to_start = threading.Timer(_STREAM_SEND_DELAY, _flush_stream_send)
            timer_to_start.daemon = True
            _STREAM_SEND_TIMER = timer_to_start

    if pending:
        emit(pending)
    if send_now:
        emit(send_now)
    if timer_to_start:
        timer_to_start.start()


def estream_end(f, c):
    _flush_stream_send()
    emit({"type": "stream_end", "file": f, "content": c})

def ephase(payload):      emit({**payload, "type":"phase"})
def echat(text):          emit({"type":"agent_msg",    "text":text})
def ememory(stats):       emit({"type":"memory",       **stats})
def emongo(payload):      emit({**payload, "type":"mongo"})
def ecommand(payload):    emit({**payload, "type":"command"})


def ecreds(accounts, source="plan", verified=None):
    """The generated app's demo accounts, for AgentForge's own UI."""
    emit({"type": "demo_accounts", "accounts": accounts,
          "source": source, "verified": verified})


_cur_stream = {"name": None, "buf": ""}

def on_token(token: str):
    if token.startswith("\x00START:"):
        fname = token[7:]
        _cur_stream["name"] = fname
        _cur_stream["buf"]  = ""
        estream_start(fname)
    elif token == "\x00END":
        fname = _cur_stream["name"]
        content = _cur_stream["buf"]
        estream_end(fname, content)
        _cur_stream["name"] = None
        _cur_stream["buf"]  = ""
    else:
        _cur_stream["buf"] += token
        estream(_cur_stream["name"] or "generating…", token)


def ensure_model(model: str) -> bool:
    """Check Ollama tags; pull model if missing."""

    if is_cloud_model(model):
        via = "API key" if ollama.api_key else \
              "signed-in Ollama" if ollama.signed_in() else None
        if via:
            elog("INFO", f"   ☁️  Cloud model ready: {model} via {via} "
                         f"(ctx {max_context(model):,})")
        else:

            elog("WARN", f"   ☁️  {model}: no API key and Ollama isn't signed "
                         f"in — trying anyway")
        return True

    if ollama.has_model(model):
        elog("INFO", f"   ✅ Model ready: {model}")
        return True

    elog("INFO", f"   📥 Pulling {model} from Ollama (first time only)…")
    ok = ollama.pull(model, on_progress=lambda p: elog("INFO", f"   📥 {model}: {p}%"))
    if ok:
        elog("INFO", f"   ✅ {model} pulled!")
    else:
        elog("ERROR", f"   ❌ Pull failed: {model}")
    return ok

def stop_model(model: str):
    """Unload model from VRAM immediately after use."""
    if is_cloud_model(model):
        return
    ollama.unload(model)
    elog("INFO", f"   🗑️  Unloaded {model}")

def _deps_ready(proj_dir: Path) -> bool:
    """Are node_modules already correct for this package.json?"""
    nm = proj_dir / "node_modules"
    if not nm.is_dir():
        return False
    try:
        pkg = json.loads((proj_dir / "package.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    if not deps:
        return False

    return all((nm / name / "package.json").exists() for name in deps)


def ensure_node_deps(proj_dir: Path) -> bool:
    if _deps_ready(proj_dir):
        return True

    first = not (proj_dir / "node_modules").is_dir()
    elog("INFO", "📦 Installing dependencies (npm install)…")

    from qa_agent.harness import NPM_LOCK
    with NPM_LOCK:
        try:
            r = cancel.run(
                [NPM_BIN, "install", "--no-audit", "--no-fund",
                 "--prefer-offline", "--loglevel=error"],
                cwd=proj_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900 if first else 300,
            )
            if r.returncode == 0:
                elog("INFO", "   ✅ npm install complete")
                return True
            elog("ERROR", f"   ❌ npm install failed:\n{(r.stderr or '')[:300]}")
            return False
        except subprocess.TimeoutExpired:
            elog("ERROR", "   ❌ npm install timed out")
            return False
        except Exception as e:
            elog("ERROR", f"   ❌ npm install crashed: {e}")
            return False


def detect_stack(proj_dir: Path) -> str:
    """Which framework a generated project uses."""
    try:
        pkg = json.loads((proj_dir / "package.json").read_text(encoding="utf-8"))
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "next" in deps:
            return "next"
        if "vite" in deps:
            return "vite"
    except Exception:
        pass
    if (proj_dir / "next.config.mjs").exists() or (proj_dir / "app").is_dir():
        return "next"
    return "vite"


def _kill_proc_tree(proc):
    """Kill a dev server and its children — npm/next spawn workers."""
    if proc is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
            return

        try:
            pgid = os.getpgid(proc.pid)
            own_group = pgid != os.getpgid(0)
        except Exception:
            pgid, own_group = None, False

        if own_group:
            try:
                os.killpg(pgid, signal.SIGTERM)
                proc.wait(timeout=4)
            except Exception:
                pass
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
            return

        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
    except Exception:
        pass


def _kill_port(port: int):
    """Force-kill whatever holds a port, on Windows as well as POSIX."""
    if os.name == "nt":
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True,
                                 timeout=10).stdout
            pids = set()
            for line in out.splitlines():
                parts = line.split()

                if len(parts) >= 5 and parts[1].rsplit(":", 1)[-1] == str(port):
                    pids.add(parts[-1])
            for pid in pids - {"0", "4"}:
                subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                               capture_output=True, timeout=10)
            if pids:
                time.sleep(0.5)
        except Exception:
            pass
        return

    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5
        )
        pids = [p.strip() for p in result.stdout.strip().split() if p.strip()]
        for pid in pids:
            try: subprocess.run(["kill", "-9", pid], timeout=3, capture_output=True)
            except: pass
        if pids:
            time.sleep(0.5)
    except Exception:
        pass


def _stop_dev_proc():
    """Terminate the tracked dev server, whatever stack it is."""
    if active_vite.get("proc"):
        _kill_proc_tree(active_vite["proc"])
        active_vite["proc"] = None


def start_vite(proj_dir: Path):
    """Kill old Vite fully, then start fresh on exact DEV_PORT."""

    _stop_dev_proc()

    _kill_port(DEV_PORT)
    active_vite["stderr_lines"] = []
    active_vite["stack"] = "vite"

    def _run():
        try:
            p = subprocess.Popen(

                [NPM_BIN, "run", "dev", "--", "--port", str(DEV_PORT),
                 "--host", "--strictPort"],
                cwd=proj_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=os.environ.copy(),

                **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == "nt" else {"start_new_session": True}),
            )
            active_vite["proc"] = p

            def _stderr():
                for line in p.stderr:
                    l = line.strip()
                    if l:
                        active_vite["stderr_lines"].append(l)
                        if any(k in l for k in ["Error","error","failed","SyntaxError"]):
                            elog("WARN", f"   [vite] {l[:120]}")
            threading.Thread(target=_stderr, daemon=True).start()

            for line in p.stdout:
                l = line.strip()
                if l: elog("INFO", f"   [vite] {l}")
        except Exception as e:
            elog("ERROR", f"   Vite crashed: {e}")

    threading.Thread(target=_run, daemon=True).start()

def wait_for_vite(timeout: int = 40) -> bool:
    """Poll DEV_PORT until Vite responds HTTP 200 or timeout expires."""
    import urllib.request, urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = urllib.request.urlopen(
                f"http://127.0.0.1:{DEV_PORT}", timeout=2
            )
            if r.status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def vite_stderr() -> str:
    lines = active_vite.get("stderr_lines", [])
    err = [l for l in lines if any(k in l for k in
        ["Error","error","SyntaxError","ReferenceError","TypeError",
         "Cannot find","is not defined","failed","plugin:vite"])]
    return "\n".join(err[-40:])


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def start_next(proj_dir: Path, port: int = DEV_PORT):
    """Start `next dev` on DEV_PORT."""
    _stop_dev_proc()
    _kill_port(port)
    active_vite["stderr_lines"] = []
    active_vite["ready"] = False
    active_vite["stack"] = "next"

    next_bin = proj_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    flags = bundler_flag(proj_dir)
    if next_bin.exists():
        argv = [NODE_BIN, str(next_bin), "dev", *flags,
                "--port", str(port), "--hostname", "127.0.0.1"]
    else:
        argv = [NPM_BIN, "run", "dev", "--", *flags,
                "--port", str(port), "--hostname", "127.0.0.1"]

    env = {**os.environ,
           "NEXT_TELEMETRY_DISABLED": "1",
           "PORT": str(port),
           "NODE_ENV": "development",
           "BROWSER": "none",

           "FORCE_COLOR": "0", "NO_COLOR": "1"}

    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})

    def _run():
        try:
            p = subprocess.Popen(
                argv, cwd=proj_dir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=env, **kwargs)
            active_vite["proc"] = p

            def _pump(stream, is_err):
                for line in stream:
                    l = _strip_ansi(line).strip()
                    if not l:
                        continue
                    active_vite["stderr_lines"].append(l)
                    if len(active_vite["stderr_lines"]) > 400:
                        del active_vite["stderr_lines"][:200]

                        active_vite["dropped"] = active_vite.get("dropped", 0) + 200
                    if any(k in l for k in ("Ready in", "✓ Ready", "- Local:")):
                        active_vite["ready"] = True
                    if is_err or any(k in l for k in
                                     ("Error", "error", "Failed to compile")):
                        elog("WARN", f"   [next] {l[:140]}")
                    else:
                        elog("INFO", f"   [next] {l[:140]}")

            threading.Thread(target=_pump, args=(p.stderr, True), daemon=True).start()
            _pump(p.stdout, False)
        except Exception as e:
            elog("ERROR", f"   Next.js crashed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def wait_for_next(timeout: int = NEXT_READY_TIMEOUT, port: int = DEV_PORT) -> bool:
    """Wait for `next dev` to serve the index route."""
    import urllib.request, urllib.error

    deadline = time.time() + timeout
    live = False
    while time.time() < deadline:
        if active_vite.get("ready"):
            live = True
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                live = True
                break
        except OSError:
            pass
        proc = active_vite.get("proc")
        if proc is not None and proc.poll() is not None:
            elog("ERROR", "   ❌ Next.js dev server exited during startup")
            return False
        time.sleep(0.3)

    if not live:
        elog("ERROR", f"   ❌ Next.js did not start within {timeout}s")
        return False

    remaining = max(30, int(deadline - time.time()))
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=remaining)
        elog("INFO", "   ✅ Next.js compiled and serving")
        return True
    except urllib.error.HTTPError as e:

        elog("WARN", f"   ⚠ Next.js served HTTP {e.code} on /")
        return True
    except Exception as e:
        elog("WARN", f"   ⚠ Next.js warm-up request failed: {e}")
        return True


def next_stderr() -> str:
    """Compile errors from the Next dev server, for the LLM fix prompt."""
    lines = active_vite.get("stderr_lines", [])
    keys = ("Failed to compile", "Module not found", "Can't resolve", "⨯",
            "Error:", "SyntaxError", "ReferenceError", "TypeError",
            "is not exported from", "is not defined",
            "MongoServerSelectionError", "MongoNetworkError", "ECONNREFUSED")
    return "\n".join([l for l in lines if any(k in l for k in keys)][-40:])


def dev_log_mark() -> int:
    """Absolute position of the next line the dev server will print."""
    return (active_vite.get("dropped", 0)
            + len(active_vite.get("stderr_lines", [])))


_DEV_NOISE = re.compile(
    r"^\s*(?:[○✓⚡]|- Local:|- Network:|Ready in\b|Compiling\b|✓ Compiled\b"
    r"|GET .*\b[23]\d\d in\b|POST .*\b[23]\d\d in\b|Attention:|▲ Next\.js)")


def dev_log_since(mark: int, limit: int = 60) -> str:
    """Everything the dev server printed since `mark`."""
    lines = active_vite.get("stderr_lines", [])
    start = max(0, mark - active_vite.get("dropped", 0))
    fresh = [l for l in lines[start:] if not _DEV_NOISE.match(l)]
    return "\n".join(fresh[-limit:])


def start_dev_server(proj_dir: Path, stack: str = None):
    """Dispatch to the right dev server for the project's stack."""
    stack = stack or detect_stack(proj_dir)

    active_vite["dir"] = str(Path(proj_dir).resolve())

    if stack == "next":
        start_next(proj_dir)
    else:
        start_vite(proj_dir)


def _dev_alive(timeout: float = 2.0) -> bool:
    """Is the dev server answering right now?"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{DEV_PORT}/",
                                    timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def wait_for_dev(stack: str, timeout: int = None) -> bool:
    if stack == "next":
        return wait_for_next(timeout or NEXT_READY_TIMEOUT)
    return wait_for_vite(timeout or 40)


def dev_stderr(stack: str) -> str:
    return next_stderr() if stack == "next" else vite_stderr()


class UIBuilder(BuilderAgent):
    """Thin wrapper — overrides _on_write and _install_deps to emit UI events."""

    def _on_write(self, fname: str, sz: str, content: str):
        efile(fname, sz, content)

    def _install_deps(self) -> bool:
        estep("install", "active")
        eprog("npm install…", 60)
        elog("INFO", "📦 npm install…")
        try:
            r = cancel.run(
                [NPM_BIN, "install"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if r.returncode == 0:
                estep("install", "done")
                eprog("Dependencies ready", 75)
                elog("INFO", "   ✅ npm install complete")
                return True
            estep("install", "error")
            elog("ERROR", f"   npm failed: {r.stderr[:200]}")
            return False
        except FileNotFoundError:
            estep("install", "error")
            elog("ERROR", f"   npm binary not found at: {NPM_BIN}")
            return False
