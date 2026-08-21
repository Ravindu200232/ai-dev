"""Focused lifecycle responsibilities for MongoManager."""
from .mongo_common import *


class MongoManagerLifecycleMixin:
    def _clear_stale_lock(self):
        """A hard kill leaves mongod.lock behind; WiredTiger is crash-safe."""
        lock = self.data_dir / "mongod.lock"
        if lock.exists() and not self.is_port_open():
            try:
                lock.unlink()
                self._log("INFO", "   🍃 Cleared a stale mongod.lock")
            except OSError:
                pass

    def start(self, timeout: int = 90) -> bool:
        binary = self.binary or self.find_binary()
        if binary is None:
            binary = self.download_binary()
        self.binary = binary

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_lock()

        cmd = [str(binary), "--dbpath", str(self.data_dir),
               "--port", str(self.port), "--bind_ip", "127.0.0.1",

               "--wiredTigerCacheSizeGB", "0.25"]

        kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                  if sys.platform.startswith("win")
                  else {"start_new_session": True})

        self._log("INFO", f"🍃 Starting MongoDB on 127.0.0.1:{self.port}")
        self._saw_ready = False
        self._out_lines = []
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(self.home), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", **kwargs)
        except Exception as e:
            self.reason = f"could not launch mongod: {e}"
            self._log("ERROR", f"   ❌ {self.reason}")
            return False

        try:
            self.pid_file.write_text(str(self.proc.pid), encoding="utf-8")
        except OSError:
            pass

        threading.Thread(target=self._drain, daemon=True).start()
        return self.wait_ready(timeout)

    def _drain(self):
        try:
            for line in self.proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                self._out_lines.append(line)
                if len(self._out_lines) > 400:
                    del self._out_lines[:200]
                if "Waiting for connections" in line or '"id":23016' in line:
                    self._saw_ready = True
        except Exception:
            pass

    def wait_ready(self, timeout: int = 90) -> bool:
        """Ready = the log marker, or two consecutive TCP accepts."""
        deadline = time.time() + timeout
        hits = 0
        while time.time() < deadline:
            if self._saw_ready:
                self._ready(); return True
            hits = hits + 1 if self.is_port_open() else 0
            if hits >= 2:
                self._ready(); return True
            if self.proc and self.proc.poll() is not None:
                self.reason = self._diagnose()
                self._log("ERROR", f"   ❌ mongod exited: {self.reason}")
                self._status("error", error=self.reason[:300])
                return False
            time.sleep(0.25)

        self.reason = f"mongod did not become ready within {timeout}s"
        self._log("ERROR", f"   ❌ {self.reason}")
        self._status("error", error=self.reason)
        return False

    def _ready(self):
        self.available = True
        self._log("INFO", f"   ✅ MongoDB ready on 127.0.0.1:{self.port}")
        self._status("running", port=self.port, version=self.version)

    def _diagnose(self) -> str:
        tail = "\n".join(self._out_lines[-8:])
        code = self.proc.returncode if self.proc else None
        if "Address already in use" in tail:
            return "port 27017 is already in use"
        if "DBPathInUse" in tail or "Unable to lock file" in tail:
            return "data directory is locked by another mongod"
        if code in (-4, 132) or (code or 0) & 0xFFFFFFFF == 0xC000001D:
            return ("this CPU lacks AVX support, which MongoDB 5.0+ requires — "
                    "set a MONGODB_URI in Settings to use an external server")
        if "vcruntime" in tail.lower() or (code or 0) & 0xFFFFFFFF == 0xC0000135:
            return ("the Microsoft Visual C++ runtime is missing — install it, "
                    "or set a MONGODB_URI in Settings")
        return tail[:400] or f"exit code {code}"

    def stop(self):

        if not self.proc:
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                               capture_output=True, timeout=15)
            else:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self._log("INFO", "   🍃 MongoDB stopped")
        except Exception:
            pass
        finally:
            self.proc = None
            self.available = False
            self.pid_file.unlink(missing_ok=True)
            self._status("off")

    def ensure_running(self) -> bool:
        """Make a MongoDB available."""
        if get_uri_override():
            self.available = True
            self.override = True
            self.reason = ""
            self._log("INFO", "🍃 Using MONGODB_URI from Settings")
            self._status("external")
            return True

        with self._lock:

            self.override = False
            if self.proc and self.proc.poll() is None:
                self.available = True
                return True
            self.adopted = False
            self.available = False

            if self.is_port_open():
                self.available = True
                self.adopted = True
                self._log("INFO", f"   ✅ Adopting the MongoDB already on "
                                  f":{self.port}")
                self._status("running", port=self.port)
                return True

            deadline = time.time() + BUDGET_S
            try:
                ok = self.start(timeout=min(90, max(10, int(deadline - time.time()))))
                if not ok and "locked" in (self.reason or ""):
                    self._clear_stale_lock()
                    ok = self.start(timeout=45)
            except Exception as e:
                self.reason = str(e)
                self._log("ERROR", f"   ❌ MongoDB setup failed: {e}")
                ok = False

            self.available = ok
            if not ok:
                self._log("WARN", "   ⚠ Continuing without a database — the app "
                                  "will still be generated, but DB pages will "
                                  "error until MongoDB is available.")
                self._log("WARN", "      Fix: set a MONGODB_URI in Settings.")
                self._status("error", error=self.reason[:300])
            return ok


