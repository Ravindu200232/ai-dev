"""Focused data responsibilities for MongoManager."""
from .mongo_common import *


class MongoManagerDataMixin:
    def reset_project_db(self, project_dir: Path, node_bin: str = "node") -> dict:
        """Drop a generated app's database so its seed runs again."""
        project_dir = Path(project_dir)
        if get_uri_override():
            return {"ok": False, "error": "MONGODB_URI is set in Settings — "
                                          "AgentForge will not touch your own database"}
        env = project_dir / ".env.local"
        if not env.exists():
            return {"ok": False, "error": "no .env.local"}
        try:
            db = ""
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("MONGODB_DB="):
                    db = line.split("=", 1)[1].strip()
        except OSError as e:
            return {"ok": False, "error": str(e)}

        if not db.startswith("agentforge_"):
            return {"ok": False, "error": f"refusing to drop '{db}' — not an "
                                          f"AgentForge-managed database"}

        script = project_dir / ".agentforge-reset.mjs"
        try:
            script.write_text(self._RESET_SCRIPT, encoding="utf-8")
            r = subprocess.run([node_bin, script.name], cwd=str(project_dir),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
            out = (r.stdout or "").strip().splitlines()
            data = json.loads(out[-1]) if out else {}
            if r.returncode != 0 or not data.get("ok"):
                return {"ok": False, "error": data.get("error")
                        or (r.stderr or "reset failed")[:200]}
            self._log("WARN", f"   🍃 Dropped database {db} "
                              f"({data.get('dropped', 0)} collections) — the "
                              f"app will re-seed on next load")
            return data
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            script.unlink(missing_ok=True)

    def prefetch(self) -> bool:
        """Download the binary without starting it — the Settings button."""
        try:
            if self.find_binary():
                self._log("INFO", "   ✅ mongod is already available")
                return True
            self.binary = self.download_binary()
            return True
        except Exception as e:
            self._log("ERROR", f"   ❌ MongoDB download failed: {e}")
            self._status("error", error=str(e))
            return False

    def uri_for(self, project: str) -> str:
        override = get_uri_override()
        if override:
            return override
        return f"mongodb://127.0.0.1:{self.port}/{db_name_for(project)}"

    def status(self) -> dict:
        binary = self.binary or self.find_binary()
        return {
            "available": self.available,
            "running": self.is_running_now(),

            "external": self.adopted,
            "override": bool(get_uri_override()),
            "ours": bool(self.proc and self.proc.poll() is None),
            "downloaded": bool(binary),
            "binary": str(binary) if binary else "",
            "version": self.version or "",
            "port": self.port,
            "reason": self.reason,
        }

    def is_running_now(self) -> bool:
        return self.is_port_open()


