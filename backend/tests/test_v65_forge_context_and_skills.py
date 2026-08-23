"""The context window: what it keeps, what it folds away, and what it loads."""
from __future__ import annotations

from forge import skills as skillkit
from forge.compaction import (compact, drop_repeats, fallback_summary,
                              hard_clip, safe_tail, shrink_tool_output,
                              summarise_middle, summariser)
from forge.context import Conversation
from forge.tokens import clip, estimate, total_tokens


def _tool(name="read_file", body="ok"):
    return {"role": "tool", "name": name, "content": body}


def _call(name="read_file"):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": "{}"}}]}


def _busy(turns=8, noise=300):
    convo = Conversation(system="architect", goal="build a shop", budget=400)
    for index in range(turns):
        convo.add_message(_call())
        convo.add_message(_tool(body=f"created app/page{index}.tsx\n" + "x " * noise))
    return convo


def test_estimating_a_window_counts_the_tool_calls_too():
    assert estimate("x" * 400) == 100
    assert total_tokens([_call()]) > total_tokens([{"role": "user", "content": ""}])


def test_clip_says_how_much_it_cut():
    assert "characters cut" in clip("y" * 1_000, 10)
    assert clip("short", 100) == "short"


def test_the_system_prompt_and_the_goal_are_never_dropped():
    convo = _busy()
    compact(convo)
    roles = [m["role"] for m in convo.pinned()]
    assert roles[0] == "system" and roles[1] == "user"
    assert convo.pinned()[1]["content"] == "build a shop"


def test_compaction_brings_a_run_back_under_budget():
    convo = _busy()
    assert convo.over_budget()
    report = compact(convo)
    assert not convo.over_budget()
    assert report["saved"] > 0 and report["after"] < report["before"]


def test_what_was_written_survives_the_compaction():
    """The noise may go. What the agent created may not."""
    convo = _busy()
    compact(convo)
    kept = " ".join(str(m.get("content") or "") for m in convo.messages())
    for index in range(8):
        assert f"app/page{index}.tsx" in kept
    assert convo.summaries, "the folded-away turns must leave a summary behind"


def test_old_tool_output_is_trimmed_and_recent_output_is_not():
    turns = [_tool(body="noise " * 400) for _ in range(10)]
    turns[-1]["content"] = "the answer that still matters " * 40
    trimmed, changed = shrink_tool_output(turns, keep_recent=3)
    assert changed >= 1
    assert trimmed[-1]["content"] == turns[-1]["content"]
    assert len(trimmed[0]["content"]) < len(turns[0]["content"])


def test_a_repeated_tool_answer_is_only_kept_once():
    turns = [_tool(body="created a.ts\nsame answer"), _tool(body="x"),
             _tool(body="created a.ts\nsame answer")]
    deduped, changed = drop_repeats(turns)
    assert changed == 1
    # The pointer keeps the first line, so the fact is not lost.
    assert deduped[0]["content"].startswith("created a.ts")
    assert "identical to the later" in deduped[0]["content"]
    assert deduped[2]["content"] == turns[2]["content"]


def test_the_kept_tail_never_starts_with_an_orphan_tool_result():
    turns = [_call(), _tool(), _call(), _tool(), _tool()]
    start = safe_tail(turns, keep_recent=2)
    assert turns[start]["role"] != "tool"


def test_a_summary_of_the_middle_replaces_it():
    convo = _busy(turns=6)
    kept = len(convo.turns)
    assert summarise_middle(convo, keep_recent=2)
    assert len(convo.turns) < kept and convo.summaries


def test_the_fallback_summary_names_files_commands_and_errors():
    text = fallback_summary([
        _tool(body="created lib/db.ts"),
        _tool(name="run_command", body="$ npm run build\n[exit 1]\nType error"),
        {"role": "assistant", "content": "the build is red"}])
    assert "lib/db.ts" in text
    assert "npm run build" in text and "exit 1" in text
    assert "Last conclusion" in text


def test_a_model_summariser_is_used_when_one_is_given():
    asked = {}

    def ask(system, user):
        asked["user"] = user
        return "the model's own summary"

    convo = _busy(turns=4)
    compact(convo, summariser(ask))
    assert "the model's own summary" in " ".join(convo.summaries)
    assert "assistant:" in asked["user"] or "tool:" in asked["user"]


def test_a_summariser_that_throws_falls_back_rather_than_failing():
    def broken(system, user):
        raise RuntimeError("the daemon is down")

    convo = _busy(turns=4)
    compact(convo, summariser(broken))
    assert convo.summaries and not convo.over_budget()


def test_summaries_roll_up_instead_of_stacking():
    convo = Conversation(system="s", goal="g", budget=10_000)
    for index in range(6):
        convo.note(f"round {index}")
    assert len(convo.summaries) <= 3
    assert "round 0" in " ".join(convo.summaries)
    assert "round 5" in " ".join(convo.summaries)


def test_pressure_reports_how_full_the_window_is():
    convo = Conversation(system="s", goal="g", budget=100)
    assert convo.pressure() < 1
    convo.add("assistant", "x" * 8_000)
    assert convo.pressure() > 1 and convo.stats()["turns"] == 1


def test_skills_are_loaded_only_when_the_task_mentions_them():
    pool = skillkit.load()
    names = {s.name for s in pool}
    assert {"nextjs", "vitest", "playwright"} <= names

    picked = lambda task: [s.name for s in skillkit.select(task, pool)]
    assert "vitest" in picked("write unit tests and mock fetch")
    assert "playwright" in picked("an e2e browser journey for login")
    assert "nextjs" in picked("add an app router page with a form")
    assert picked("summarise this pdf invoice") == []


def test_rendering_skills_stays_within_a_sane_size():
    rendered = skillkit.for_task("write vitest unit tests for the items page")
    assert "--- skill: vitest ---" in rendered
    assert estimate(rendered) < 2_600


def test_a_skill_file_declares_its_triggers():
    vitest = next(s for s in skillkit.load() if s.name == "vitest")
    assert vitest.description and vitest.triggers
    assert vitest.score("Write a VITEST unit test") >= 2


def test_one_message_larger_than_the_whole_budget_is_cut_not_dropped():
    convo = Conversation(system="s", goal="g", budget=200)
    convo.add_message(_tool(body="created lib/db.ts\n" + "noise " * 2_000))
    convo.add("assistant", "read the store")
    report = compact(convo)
    assert convo.tokens() <= convo.budget
    assert report["clipped"] > 0
    # The turn is still there, just shorter.
    assert len(convo.turns) == 2
    assert "created lib/db.ts" in convo.turns[0]["content"]


def test_hard_clip_gives_up_rather_than_spinning():
    convo = Conversation(system="s", goal="g", budget=1)
    convo.add_message(_call())
    assert hard_clip(convo) < 60


def test_a_pinned_head_bigger_than_the_budget_is_reported_not_papered_over():
    convo = Conversation(system="s" * 4_000, goal="g", budget=50)
    convo.add("assistant", "x" * 800)
    report = compact(convo)
    assert report["over_pinned"] is True
    assert convo.turns, "turns must not be thrown away over a pinned overflow"
