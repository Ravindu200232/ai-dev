# Shared helpers for unit-test repair and reporting.
MAX_QA_FIX = 4
QA_ROUND_CEILING = 10
QA_DEADLINE = 1800


MAX_QA_STALLS = 1

MAX_QA_DEAD = 4


def _qa_skip_note(qa, why: str) -> None:
    """Say why the stage stopped early, without calling the tests skipped."""
    elog("INFO", f"   🧪 {why}")
    if qa and getattr(qa, "report", None) is not None:
        qa.report.stopped_reason = why


QA_TIERS = {
    0: "the usual repair",
    1: "the reason the last write was refused, and more of the file's context",
    2: "permission to fix the component instead of the test",

    3: "the harness itself as a suspect, and permission to name it",
}
MAX_QA_TIER = max(QA_TIERS)


QA_FIX_WORKERS = 4


def _qa_callbacks() -> dict:
    """Bridge QA progress and browser activity to the Studio."""
    cb = _analyzer_callbacks()
    cb["on_file_written"] = lambda path, size, content: efile(path, size, content)
    cb["on_e2e_event"] = lambda payload: emit({**payload, "type": "e2e_event"})
    return cb


def _qa_skip(qa, why):
    elog("WARN", f"   ⚠ Unit tests skipped — {why}")
    if qa:
        qa.report.skipped_reason = why
    emit({"type": "test_result", "status": "skip",
          "msg": "Unit tests skipped", "detail": why})


def read_qa_results(proj_name: str) -> dict:
    """Everything the QA stages left on disk for one project."""
    proj_dir = PROD_DIR / proj_name
    if not proj_dir.is_dir():
        return {"error": f"no such project: {proj_name}"}
    qa_dir = proj_dir / ".agentforge" / "qa"

    def load(path, default=None):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    out = {"project": proj_name, "have": {}}

    out["vitest"] = load(qa_dir / "vitest.json")
    out["have"]["vitest"] = out["vitest"] is not None

    out["manifest"] = load(qa_dir / "manifest.json", {})
    out["have"]["manifest"] = bool(out["manifest"])

    out["report"] = load(qa_dir / "report.json")
    out["have"]["report"] = out["report"] is not None

    history = []
    try:
        for line in (qa_dir / "history.jsonl").read_text(
                encoding="utf-8").splitlines():
            if line.strip():
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    out["history"] = history
    out["have"]["history"] = bool(history)

    lh = load(proj_dir / ".agentforge" / "lighthouse.json")
    if lh:
        cats = lh.get("categories") or {}
        audits = lh.get("audits") or {}
        out["performance"] = {
            "scores": {k: round((v or {}).get("score") * 100)
                       for k, v in cats.items()
                       if isinstance((v or {}).get("score"), (int, float))},
            "metrics": {k: (audits.get(k) or {}).get("displayValue")
                        for k in ("largest-contentful-paint",
                                  "cumulative-layout-shift",
                                  "total-blocking-time",
                                  "first-contentful-paint", "speed-index")
                        if audits.get(k)},
            "fetchTime": lh.get("fetchTime", ""),
            "runtimeError": (lh.get("runtimeError") or {}).get("code", ""),
        }
    out["have"]["performance"] = "performance" in out

    tests = {}
    for sub in ("tests/unit", "tests/e2e", "tests/quarantine"):
        d = proj_dir / sub
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.suffix in (".js", ".jsx"):
                try:
                    rel = str(f.relative_to(proj_dir)).replace("\\", "/")
                    tests[rel] = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
    out["tests"] = tests
    out["have"]["tests"] = bool(tests)
    return out


