# Routes AgentForge UI and API requests.
class UIHandler(SimpleHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **k):
        self._no_cache = False

        super().__init__(*a, directory=str(BASE_DIR), **k)

    def log_message(self, *a): pass

    def end_headers(self):

        if self._no_cache:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self._no_cache = False
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, payload, code=200):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _split(self):
        """(is_ours, path_without_the_prefix)."""
        path = urlsplit(self.path).path
        if path == AGENTFORGE_PREFIX or path.startswith(AGENTFORGE_PREFIX + "/"):
            return True, path[len(AGENTFORGE_PREFIX):] or "/"
        return False, path

    def _is_websocket(self) -> bool:
        return ("upgrade" in self.headers.get("Connection", "").lower()
                and self.headers.get("Upgrade", "").lower() == "websocket")

    def do_GET(self):
        ours, path = self._split()
        if not ours:
            if self._is_websocket():
                return self._proxy_websocket()

            if path == "/" and self.headers.get("Sec-Fetch-Dest") == "document":
                self.send_response(302)
                self.send_header("Location", AGENTFORGE_PREFIX + "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._proxy("GET")
        if path.startswith("/api/"):
            return self._guarded(self._api_get, path[4:])
        return self._serve_ui(path)

    def do_HEAD(self):
        ours, path = self._split()
        if not ours:
            return self._proxy("HEAD")

        del path
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self):     self._proxy_or_405("PUT")
    def do_PATCH(self):   self._proxy_or_405("PATCH")
    def do_DELETE(self):  self._proxy_or_405("DELETE")

    def _proxy_or_405(self, method):
        ours, _ = self._split()
        if ours:
            return self._json({"error": "method not allowed"}, 405)
        self._proxy(method)

    def _serve_ui(self, path):
        """There is no UI on this port any more — say so, and point at the one."""
        del path
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<title>AgentForge</title>"
            "<body style=\"font:14px/1.6 system-ui;max-width:34rem;margin:12vh auto;"
            "padding:0 1.5rem;color:#e6e6e6;background:#111\">"
            "<h1 style=\"font-size:1.1rem\">AgentForge is not served on this port</h1>"
            f"<p>This is the backend API on :{UI_PORT}. The studio runs separately —"
            " <a style=\"color:#22d3ee\" href=\"http://localhost:3000/__agentforge\">"
            "http://localhost:3000/__agentforge</a></p>"
            "<p style=\"color:#888\">Start both with <code>start.bat</code>.</p>"
        ).encode()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_get(self, path):

        if path.startswith("/srs/"):
            return self._proxy_srs("GET", path[4:])
        if path == "/srs-status":
            return self._json(srs_status())

        if path.startswith("/jobs/"):
            return self._json(job_poll(path[len("/jobs/"):]))

        if path.startswith("/deploy/jobs/"):
            try:
                return self._json(deploy_job_poll(path[13:].strip("/")))
            except KeyError as e:
                return self._json({"error": str(e)}, 404)
        if path.startswith("/deploy/"):
            return self._proxy_deploy("GET", "/api" + path[7:])
        if path == "/deploy-status":
            return self._json(deploy_status())
        if path.startswith("/deploy-results/"):
            return self._json(read_deploy_results(path[16:].strip("/")))
        if path == "/projects":
            self._json(list_projects())
        elif path == "/image-check":

            agent = image_agent()
            host = agent.base_url()
            launcher = _fooocus_launcher()

            import socket
            lan = ""
            if load_settings().get("lan_access"):
                try:
                    lan = f"http://{socket.gethostbyname(socket.gethostname())}:{UI_PORT}"
                except OSError:
                    lan = ""
            self._json({"enabled": agent.enabled, "available": bool(host),
                        "host": host or "", "lan_url": lan,
                        "launcher": launcher,
                        "can_start": bool(launcher and not host),
                        "lan_access": bool(load_settings().get("lan_access"))})
        elif path == "/models":

            self._json(ollama.catalog())
        elif path == "/settings":
            s = load_settings()
            key = ollama.api_key
            uri = str(s.get("mongodb_uri", "")).strip()
            self._json({
                "ollama_host": ollama.host,
                "cloud_enabled": bool(key),

                "api_key_hint": (f"…{key[-4:]}" if key else ""),
                "local_num_ctx": s.get("local_num_ctx", max_context("llama3.1:8b")),
                "agent_model": s.get("agent_model", default_agent_model()),
                **_image_settings(),
                "mongodb_uri_set": bool(uri),
                "mongodb_uri_hint": _redact_uri(uri),
                "mongo": MONGO.status(),

                "deploy": deploy_settings_summary(),
            })
        elif path == "/mongo":
            self._json(MONGO.status())
        elif path.startswith("/files/"):
            self._json(get_project_files(path[7:].strip("/")))
        elif path.startswith("/qa/"):
            self._json(read_qa_results(path[4:].strip("/")))
        elif path.startswith("/srs-results/"):
            self._json(read_srs_results(path[13:].strip("/")))
        elif path.startswith("/qa-pdf/"):

            proj = path[8:].strip("/")
            qa = read_qa_results(proj)
            if qa.get("error"):
                return self._json(qa, 404)
            try:
                from qa_agent.report_pdf import build_qa_pdf
                out = (PROD_DIR / proj / ".agentforge" / "qa"
                       / "Test_Report.pdf")
                build_qa_pdf(qa, out, project=proj)
                self._plain(200, out.read_bytes(), "application/pdf",
                            extra=(("Content-Disposition",
                                    f'attachment; filename="{proj}-test-report.pdf"'),))
            except Exception as e:                              # noqa: BLE001
                log.exception("qa pdf")
                self._json({"error": f"the test report could not be built: {e}"}, 500)
        elif path.startswith("/srs-pdf/"):

            pdf = (PROD_DIR / path[9:].strip("/") / ".agentforge" / "srs"
                   / "SRS_latest.pdf")
            if pdf.is_file():
                self._plain(200, pdf.read_bytes(), "application/pdf",
                            extra=(("Content-Disposition",
                                    'inline; filename="SRS.pdf"'),))
            else:
                self._json({"error": "no SRS PDF for this project"}, 404)
        else:
            self._json({"error": f"unknown endpoint {path}"}, 404)

    def do_POST(self):
        ours, path = self._split()
        if not ours:
            return self._proxy("POST")

        length = int(self.headers.get("Content-Length", 0) or 0)
        self._raw = self.rfile.read(length) if length else b""
        if not path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        self._guarded(self._api_post, path[4:])

    def _guarded(self, fn, path):
        """Run an API branch and turn a crash into an answer."""
        try:
            fn(path)
        except Exception as e:
            log.exception(f"api {path}")
            elog("WARN", f"   ⚠ {path} failed: {type(e).__name__}: {e}")
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    _raw = b""

    def _body(self) -> dict:
        if not self._raw:
            return {}
        try:
            return json.loads(self._raw)
        except Exception:
            return {}

    def _api_post(self, path):

        if path.startswith("/srs/"):
            return self._proxy_srs("POST", path[4:])

        if path == "/jobs":
            body = self._body()
            try:
                return self._json(job_start(
                    str(body.get("method", "POST")).upper(),
                    str(body.get("path", "")),
                    body.get("body") or {}))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/deploy/jobs":
            body = self._body()
            try:
                return self._json(deploy_job_start(
                    str(body.get("method", "POST")).upper(),
                    "/api" + str(body.get("path", "")),
                    body.get("body") or {}))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path.startswith("/deploy/"):
            return self._proxy_deploy("POST", "/api" + path[7:])
        if path == "/deploy-start":
            body = self._body()
            try:
                return self._json(start_deployment(
                    str(body.get("project", "")).strip(),
                    str(body.get("target", "vercel")).strip(),
                    body))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/discard-srs":
            sid = str(self._body().get("srs_id", "")).strip()
            out = discard_srs(sid)
            return self._json(out, 400 if out.get("error") else 200)
        if path == "/build/cancel":
            out = cancel.request()
            return self._json(out, 200 if out.get("ok") else 409)
        if path == "/build":
            body = self._body()
            threading.Thread(
                target=run_pipeline,
                args=(body.get("prompt",""),
                      body.get("refine_model", DEFAULT_REFINE),
                      body.get("build_model",  DEFAULT_BUILD)),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/resume":
            body = self._body()
            threading.Thread(
                target=run_agent_pipeline,
                args=("", body.get("model") or default_agent_model(),
                      _think_flag(body),
                      (body.get("qa_model") or "").strip(),
                      body.get("project", "").strip()),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/delete-project":
            body = self._body()
            out = delete_project(str(body.get("project", "")))
            self._json(out, 400 if out.get("error") else 200)
        elif path == "/save-file":
            body = self._body()
            out = save_project_file(str(body.get("project", "")),
                                    str(body.get("path", "")),
                                    str(body.get("content", "")))
            self._json(out, 400 if out.get("error") else 200)
        elif path == "/image-start":

            why = start_fooocus()
            if why:
                return self._json({"error": why}, 503)
            self._json({"ok": True, "launcher": _fooocus_launcher()})
        elif path == "/logo-prompt":

            body = self._body()
            idea = str(body.get("prompt", "")).strip()
            if not idea:
                return self._json({"error": "prompt is required"}, 400)
            model = (body.get("model") or default_agent_model()).strip()
            try:
                r = ollama.chat(model, [
                    {"role": "system", "content": LOGO_PROMPT_SYSTEM},
                    {"role": "user", "content": idea[:1200]},
                ], options={"temperature": 0.7}, timeout=120)
                text = ((r.get("message") or {}).get("content") or "").strip()

                text = text.splitlines()[-1].strip().strip('"').strip("'")
            except Exception as e:
                log.debug(f"logo prompt: {e}")
                text = ""
            self._json({"ok": True, "prompt": text})
        elif path == "/tune":

            body = self._body()
            text = str(body.get("prompt", "")).strip()
            if not text:
                return self._json({"error": "prompt is required"}, 400)
            model = (body.get("model") or default_agent_model()).strip()
            element = body.get("element") or {}
            route = str(body.get("route") or element.get("route") or "/")
            tuned = tune_instruction(text, element, route, model,
                                     project=str(body.get("project") or ""))
            self._json({"ok": True, "prompt": tuned,
                        "changed": tuned.strip() != text.strip()})
        elif path == "/themes":

            body = self._body()
            idea = str(body.get("prompt", "")).strip()
            srs_id = str(body.get("srs_id", "")).strip()
            model = (body.get("model") or default_agent_model()).strip()
            if not idea and not srs_id:
                return self._json({"error": "prompt or srs_id is required"}, 400)

            brief, doc, plan, source = "", {}, {}, "the prompt"
            if srs_id:
                r = _srs_get(f"/projects/{srs_id}/plan", timeout=30)
                if r is not None:
                    try:
                        body_ = r.json() or {}
                        brief = str(body_.get("markdown") or "").strip()
                        plan = body_.get("plan") or {}
                    except Exception:
                        brief, plan = "", {}
                if brief:
                    source = "plan.md"
                else:
                    try:
                        raw = json.loads((PROD_DIR / ".srs" / srs_id
                                          / "srs_latest.json").read_text("utf-8"))
                        doc = raw.get("srs_document") or raw
                    except Exception:
                        doc = {}
                    if plan or doc:
                        source = "the SRS plan"
            if not brief:
                brief = themekit.brief_from_plan(plan, doc, idea)

            app_name = _srs_app_name(srs_id) or ""
            settings = load_settings()
            directions = themekit.random_directions(themekit.COUNT)
            elog("INFO", f"   🎨 drawing {len(directions)} designs of the whole "
                         f"app from {source} — {model}")

            out = []
            why = []
            lock = threading.Lock()

            # Five designs in parallel, not in a row: the calls.
            workers = [threading.Thread(
                target=_draw_design,
                args=(d, brief, app_name, model, settings, out, lock, why),
                daemon=True) for d in directions]
            for w in workers:
                w.start()
            for w in workers:
                w.join(timeout=THEME_TIMEOUT_S + 120)
            # Keep the picker's order stable whichever thread finished first
            out.sort(key=lambda d: d.get("index", 99))

            if not out:
                busy = sum(1 for row in why if row.get("busy"))
                detail = (f"{model} was busy for all {len(directions)} designs — "
                          f"they are five calls at once, which is the heaviest "
                          f"moment in a build. Try again, or set a smaller "
                          f"model for design work."
                          if busy and busy >= len(why) else
                          "; ".join(str(row.get("reason", ""))[:90]
                                    for row in why[:3])
                          or "the model returned nothing usable")
                elog("WARN", f"   ⚠ no design came back — {detail}")
                return self._json({"error": "no design came back",
                                   "detail": detail,
                                   "busy": busy,
                                   "attempted": len(directions)}, 502)
            key = stage_designs(out, source=source, srs_id=srs_id, idea=idea,
                                model=model)
            if key:
                elog("INFO", f"   💾 {len(out)} design(s) saved under "
                             f"{_designs_dir().name}/{key} — moved into the "
                             f"project when the build starts")
            # The header line: the pages the fullest design contains.
            fullest = max(out, key=lambda d: d.get("pages", 0))
            names = fullest.get("page_names") or []
            if why:
                elog("WARN", f"   ⚠ {len(why)} of {len(directions)} designs "
                             f"did not come back; the picker shows the "
                             f"{len(out)} it has")
            self._json({"ok": True,
                        "page": " · ".join(names[:8]) + ("…" if len(names) > 8 else ""),
                        "pages": names,
                        "source": source,
                        "designs": key,
                        "attempted": len(directions),
                        "missing": len(why),
                        "themes": out})
        elif path == "/image":

            body = self._body()
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                return self._json({"error": "prompt is required"}, 400)
            agent = image_agent()
            if not agent.enabled:
                return self._json({"error": "image generation is switched off"}, 409)
            if not agent.available():
                return self._json(
                    {"error": "no Fooocus is answering — start it, or set "
                              "image_host to the machine that runs it"}, 503)
            name = str(body.get("name", "")) or agent.slug(prompt)
            proj = str(body.get("project", "")).strip()
            out = ((PROD_DIR / proj / "public" / "generated") if proj
                   else (LOGS_DIR / "images")) / f"{name}.png"
            ok = agent.generate(prompt, out,
                                aspect=str(body.get("aspect", "landscape")),
                                seed=int(body.get("seed", 0) or 0),
                                force=bool(body.get("force")))
            if not ok:
                return self._json({"error": "generation failed"}, 502)

            self._json({"ok": True, "file": str(out), "name": name,
                        "data_uri": preview_uri(out),
                        "url": (f"/generated/{name}.png" if proj else "")})
        elif path == "/image-upload":

            body = self._body()
            name = _safe_stem(body.get("name") or body.get("filename"), "upload")
            proj = _safe_stem(body.get("project", ""), "")
            out = ((PROD_DIR / proj / "public" / "generated") if proj
                   else (LOGS_DIR / "images")) / f"{name}.png"

            why = save_uploaded_image(body.get("data_base64", ""), out)
            if why:
                return self._json({"error": why}, 400)

            self._json({"ok": True, "file": str(out), "name": name,
                        "data_uri": preview_uri(out),
                        "url": (f"/generated/{name}.png" if proj else "")})
        elif path == "/agent-build":
            body = self._body()
            threading.Thread(
                target=run_agent_pipeline,
                args=(body.get("prompt", ""),
                      body.get("model") or default_agent_model(),
                      _think_flag(body),
                      (body.get("qa_model") or "").strip(),
                      "", str(body.get("logo", "")).strip(),
                      str(body.get("srs_id", "")).strip()),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/pencil-edit":
            body = self._body()
            threading.Thread(
                target=run_pencil_edit,
                args=(body.get("project", ""), body.get("prompt", ""), body,
                      body.get("model") or default_agent_model(),
                      _think_flag(body)),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/element-edit":
            body = self._body()
            threading.Thread(
                target=run_element_edit,
                args=(body.get("project", ""), body.get("prompt", ""),
                      body.get("element") or {},
                      body.get("model") or default_agent_model(),
                      _think_flag(body)),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/image-edit":

            body = self._body()
            threading.Thread(
                target=run_image_edit,
                args=(body.get("project", ""), body.get("prompt", ""),
                      body.get("element") or {},
                      body.get("model") or default_agent_model(),
                      _think_flag(body)),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/attach":

            body = self._body()
            proj = _safe_stem(body.get("project", ""), "")
            proj_dir = (PROD_DIR / proj) if proj and (PROD_DIR / proj).is_dir() else None
            got = read_attachment(str(body.get("filename", "")),
                                  body.get("data_base64", ""), proj_dir)
            text = got["text"][:ATTACH_TEXT_CAP]
            if len(got["text"]) > ATTACH_TEXT_CAP:
                text += "\n… (the rest was left out to keep the prompt workable)"
            got["text"] = text
            self._json({"ok": True, **got})
        elif path == "/image-swap":

            body = self._body()
            threading.Thread(
                target=run_image_swap,
                args=(body.get("project", ""), body.get("data_base64", ""),
                      body.get("filename", ""), body.get("element") or {}),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/undo":
            body = self._body()
            self._json(restore_snapshot(body.get("project", ""),
                                        body.get("id", "")))
        elif path == "/feature":
            body = self._body()
            threading.Thread(
                target=run_feature,
                args=(body.get("project", ""),
                      body.get("prompt", ""),
                      body.get("model") or default_agent_model(),
                      _think_flag(body),
                      (body.get("qa_model") or "").strip()),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path == "/agent-update":
            body = self._body()
            threading.Thread(
                target=run_chat,
                args=(body.get("project", ""), body.get("prompt", ""),
                      body.get("model") or default_agent_model(),
                      (body.get("route") or "").strip(), _think_flag(body),
                      (body.get("qa_model") or "").strip(),
                      _browser_console(body)),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif path.startswith("/open/"):
            proj = path[6:].strip("/")
            threading.Thread(target=_open_project, args=(proj,), daemon=True).start()
            self._json({"ok": True})
        elif path == "/mongo/prefetch":
            threading.Thread(target=MONGO.prefetch, daemon=True).start()
            self._json({"ok": True})
        elif path == "/settings":
            body = self._body()
            patch = {}
            for key in ("unsplash_key", "pexels_key"):
                if key in body:
                    patch[key] = str(body[key]).strip()
            if "ollama_api_key" in body:
                patch["ollama_api_key"] = str(body["ollama_api_key"]).strip()
            if "mongodb_uri" in body:
                patch["mongodb_uri"] = str(body["mongodb_uri"]).strip()
            if body.get("ollama_host"):
                patch["ollama_host"] = str(body["ollama_host"]).strip()
            if "lan_access" in body:
                patch["lan_access"] = bool(body["lan_access"])
            if "image_enabled" in body:
                patch["image_enabled"] = bool(body["image_enabled"])
            if "image_host" in body:
                patch["image_host"] = str(body["image_host"]).strip()
            if "image_config" in body:
                patch["image_config"] = str(body["image_config"]).strip()
            if "image_launcher" in body:
                patch["image_launcher"] = str(body["image_launcher"]).strip()
            if body.get("local_num_ctx"):
                try:
                    patch["local_num_ctx"] = max(4096, int(body["local_num_ctx"]))
                except (TypeError, ValueError):
                    pass
            if body.get("agent_model"):
                patch["agent_model"] = str(body["agent_model"]).strip()

            if "srs_model" in body:
                patch["srs_model"] = str(body["srs_model"]).strip()

            if "deploy_model" in body:
                patch["deploy_model"] = str(body["deploy_model"]).strip()
            for key in ("aws_profile", "aws_region", "aws_start_url",
                        "aws_sso_region"):
                if key in body:
                    patch[key] = str(body[key]).strip()
            if "vercel_token" in body:
                v = str(body["vercel_token"]).strip()
                patch["vercel_token"] = "" if v == "-" else v
            if "deploy_mongodb_uri" in body:
                v = str(body["deploy_mongodb_uri"]).strip()
                patch["deploy_mongodb_uri"] = "" if v == "-" else v
            ok = save_settings(patch)
            if patch.get("ollama_host"):
                ollama.host = patch["ollama_host"].rstrip("/")
            self._json({"ok": ok, "cloud_enabled": bool(ollama.api_key),
                        "cloud_reachable": ollama.cloud_reachable()
                        if ollama.api_key else False})
        elif path == "/upload-project":
            body = self._body()
            name = body.get("name", "imported")
            files = body.get("files", {})

            pname = re.sub(r"[^a-z0-9]", "", name.lower())[:20] or "imported"
            proj_dir = PROD_DIR / pname
            proj_dir.mkdir(parents=True, exist_ok=True)

            for rel_path, content in files.items():
                fp = proj_dir / rel_path
                fp.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fp.write_text(content, encoding="utf-8")
                except Exception as e:
                    log.error(f"Failed to write {rel_path}: {e}")

            self._json({"ok": True, "project": pname})
        elif path == "/update":
            body = self._body()
            threading.Thread(
                target=run_update_pipeline,
                args=(body.get("project",""),
                      body.get("prompt",""),
                      body.get("build_model", DEFAULT_BUILD)),
                daemon=True
            ).start()
            self._json({"ok": True})
        else:
            self._json({"error": f"unknown endpoint {path}"}, 404)

    def _proxy(self, method: str):
        url = f"http://127.0.0.1:{DEV_PORT}{self.path}"
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length and self.headers.get("Transfer-Encoding"):
            return self._plain(411, b"chunked request bodies are not proxied")
        body = self.rfile.read(length) if length else None

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP}

        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Proto"] = "http"
        headers["X-Forwarded-For"] = "127.0.0.1"
        headers["Accept-Encoding"] = "identity"

        try:
            r = requests.request(method, url, headers=headers, data=body,
                                 stream=True, allow_redirects=False,
                                 timeout=(2, 300))
        except requests.RequestException:
            return self._preview_unavailable()

        self.send_response(r.status_code)
        upstream_length = None

        for k, v in r.raw.headers.items():
            kl = k.lower()
            if kl in HOP_BY_HOP or kl == "content-encoding":
                continue
            if kl == "content-length":
                upstream_length = v
            if kl == "set-cookie":
                v = re.sub(r";\s*Secure", "", v, flags=re.I)
            self.send_header(k, v)
        if upstream_length is None:

            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        if method == "HEAD":
            return
        try:
            for chunk in r.raw.stream(65536, decode_content=False):
                self.wfile.write(chunk)
        except Exception:
            self.close_connection = True

    def _proxy_srs(self, method: str, path: str):
        """Hand this request to the SRS agent on SRS_PORT."""
        if SRS_API["state"] in ("off", "import-failed"):
            return self._json({"error": "the SRS agent is not running",
                               "srs": srs_status()}, 503)

        query = urlsplit(self.path).query
        url = f"http://127.0.0.1:{SRS_PORT}{path}" + (f"?{query}" if query else "")

        body = self._raw or None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP}
        headers["Accept-Encoding"] = "identity"

        try:
            r = requests.request(method, url, headers=headers, data=body,
                                 stream=True, allow_redirects=False,

                                 timeout=(2, None))
        except requests.RequestException as e:
            return self._json({"error": f"SRS agent unreachable: {e}",
                               "srs": srs_status()}, 502)

        self.send_response(r.status_code)
        upstream_length = None
        for k, v in r.raw.headers.items():
            kl = k.lower()
            if kl in HOP_BY_HOP or kl == "content-encoding":
                continue
            if kl == "content-length":
                upstream_length = v
            self.send_header(k, v)

        self.send_header("Access-Control-Allow-Origin", "*")
        if upstream_length is None:

            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        try:
            for chunk in r.raw.stream(65536, decode_content=False):
                self.wfile.write(chunk)
        except Exception:
            self.close_connection = True

    def _proxy_deploy(self, method: str, path: str):
        """Hand this request to the deployment agent on DEPLOY_PORT."""
        if DEPLOY_API["state"] in ("off", "import-failed"):
            return self._json({"error": "the deployment agent is not running",
                               "deploy": deploy_status()}, 503)

        query = urlsplit(self.path).query
        url = (f"http://127.0.0.1:{DEPLOY_PORT}{path}"
               + (f"?{query}" if query else ""))

        body = self._raw or None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP}
        headers["Accept-Encoding"] = "identity"

        try:
            r = requests.request(method, url, headers=headers, data=body,
                                 stream=True, allow_redirects=False,
                                 timeout=(2, None))
        except requests.RequestException as e:
            return self._json({"error": f"deployment agent unreachable: {e}",
                               "deploy": deploy_status()}, 502)

        self.send_response(r.status_code)
        upstream_length = None
        for k, v in r.raw.headers.items():
            kl = k.lower()
            if kl in HOP_BY_HOP or kl == "content-encoding":
                continue
            if kl == "content-length":
                upstream_length = v
            self.send_header(k, v)
        if upstream_length is None:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        if method == "HEAD":
            return
        try:
            for chunk in r.raw.stream(65536, decode_content=False):
                self.wfile.write(chunk)
        except Exception:
            self.close_connection = True

    def _plain(self, code, body: bytes, ctype="text/plain; charset=utf-8",
               extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _preview_unavailable(self):
        """The dev server is not up."""
        dest = self.headers.get("Sec-Fetch-Dest", "")
        wants_page = (dest in ("document", "iframe")
                      or "text/html" in self.headers.get("Accept", ""))
        if not wants_page:

            return self._plain(502, b"")
        page = (b"<!doctype html><meta charset=utf-8>"
                b"<title>Preview not running</title>"
                b"<style>body{font:14px system-ui;background:#0b0d10;color:#8b949e;"
                b"display:grid;place-items:center;height:100vh;margin:0}"
                b"b{color:#e6edf3;font-weight:600}</style>"
                b"<div style=text-align:center><p><b>Preview not running</b>"
                b"<p>Waiting for the dev server\xe2\x80\xa6"
                b"<p><button onclick=location.reload() style=\"font:inherit;"
                b"padding:6px 14px;border-radius:6px;border:1px solid #30363d;"
                b"background:#161b22;color:#e6edf3;cursor:pointer\">Retry</button>"
                b"</div><script>setTimeout(()=>location.reload(),2000)</script>")
        self._plain(503, page, "text/html; charset=utf-8",
                    extra=(("Retry-After", "2"), ("Cache-Control", "no-store")))

    def _proxy_websocket(self):
        """Relay the HMR socket byte for byte."""
        try:
            up = socket.create_connection(("127.0.0.1", DEV_PORT), timeout=5)
        except OSError:
            self.close_connection = True
            try:
                self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except Exception:
                pass
            return

        self.close_connection = True
        try:
            head = f"{self.command} {self.path} HTTP/1.1\r\n".encode()
            for k, v in self.headers.items():
                head += f"{k}: {v}\r\n".encode()
            up.sendall(head + b"\r\n")

            self.connection.settimeout(None)
            up.settimeout(None)

            def pump_down():
                try:
                    while True:
                        data = up.recv(65536)
                        if not data:
                            break
                        self.wfile.write(data)
                        self.wfile.flush()
                except Exception:
                    pass
                finally:
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            t = threading.Thread(target=pump_down, daemon=True)
            t.start()
            while True:

                data = self.rfile.read1(65536)
                if not data:
                    break
                up.sendall(data)
        except Exception:
            pass
        finally:
            try:
                up.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            up.close()
