from pathlib import Path

import server


def test_qa_callbacks_forward_live_e2e_events(monkeypatch):
    seen = []
    monkeypatch.setattr(server, "emit", lambda payload: seen.append(payload))
    callbacks = server._qa_callbacks()
    callbacks["on_e2e_event"]({"state": "step_done", "route": "/rooms"})
    assert seen == [{"state": "step_done", "route": "/rooms", "type": "e2e_event"}]


def test_generic_tuner_covers_feature_and_bug_requests():
    text = server.TUNE_SYSTEM.lower()
    assert "bug" in text
    assert "feature" in text
    assert "current route" in text


def test_e2e_runner_streams_real_browser_frames():
    src = (Path(__file__).parents[1] / "qa_agent" / "e2e_execution.py").read_text(encoding="utf-8")
    assert 'page.screenshot(type="jpeg"' in src
    assert '"frame": live_frame(page)' in src


def test_targeted_vitest_merge_keeps_old_suite_rows(tmp_path):
    import json
    from qa_agent.runner import VitestRunner

    qa_dir = tmp_path / ".agentforge" / "qa"
    qa_dir.mkdir(parents=True)
    old = {
        "testResults": [
            {"name": "tests/a.test.js", "status": "passed", "assertionResults": [{"title": "a", "status": "passed"}]},
            {"name": "tests/b.test.js", "status": "passed", "assertionResults": [{"title": "b-old", "status": "passed"}]},
        ]
    }
    (qa_dir / "vitest.json").write_text(json.dumps(old), encoding="utf-8")
    part = {
        "testResults": [
            {"name": "tests/b.test.js", "status": "passed", "assertionResults": [{"title": "b-new", "status": "passed"}]},
        ]
    }
    VitestRunner(tmp_path)._merge_into_report(part)
    merged = json.loads((qa_dir / "vitest.json").read_text(encoding="utf-8"))
    names = [row["name"] for row in merged["testResults"]]
    assert names == ["tests/a.test.js", "tests/b.test.js"]
    assert merged["numTotalTests"] == 2
    assert merged["numPassedTests"] == 2
    assert merged["testResults"][1]["assertionResults"][0]["title"] == "b-new"
