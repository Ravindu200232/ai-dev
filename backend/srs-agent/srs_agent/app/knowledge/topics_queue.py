"""Queue and budget helpers for interview topics."""
from __future__ import annotations

from .topics_catalog import TOPICS
from .topics_core import Topic, question_set

TOPICS_BY_KEY = {t.key: t for t in TOPICS}


MAX_VISIBLE_QUESTIONS = 25
_ASSUMED_REPEATS = 4


def topics_for(profile: str) -> list[Topic]:
    """The question set a given app profile asks, before conditions apply."""
    return [t for t in TOPICS if not t.profiles or profile in t.profiles]


def question_budget(profile: str) -> int:
    """Worst-case visible questions for a profile."""
    total = 0
    for topic in topics_for(profile):
        total += _ASSUMED_REPEATS if topic.repeats_over else 1
    return total


def validate_question_budget() -> dict[str, int]:
    """Raise if any profile could ask more than the maximum."""
    counts = {}
    over = []
    for profile in sorted({t for topic in TOPICS for t in topic.profiles}
                          | {"landing", "utility", "saas"}):
        counts[profile] = question_budget(profile)
        if counts[profile] > MAX_VISIBLE_QUESTIONS:
            over.append(f"{profile}={counts[profile]}")
    if over:
        raise ValueError(
            "question sets exceed the "
            f"{MAX_VISIBLE_QUESTIONS}-question budget: {', '.join(over)}"
        )
    return counts


def build_queue(session: dict) -> list[dict]:
    """Every question still to ask, in order."""
    queue: list[dict] = []
    answers = session.get("answers", {})
    profile = question_set(session)

    for topic in TOPICS:
        if topic.profiles and profile not in topic.profiles:
            continue
        if not topic.applies_to(session):
            continue

        if topic.repeats_over:
            for item in topic.repeats_over(session):
                key = f"{topic.key}:{item}"
                if key not in answers:
                    queue.append({"topic": topic.key, "key": key, "subject": item})
        else:
            if topic.key not in answers:
                queue.append({"topic": topic.key, "key": topic.key, "subject": None})

    return queue


def total_estimate(session: dict) -> int:
    """Asked so far + still queued."""
    asked = [k for k in session.get("asked", []) if not is_clarification(k)]
    return len(asked) + len(build_queue(session))


def is_clarification(key: str) -> bool:
    """Follow-up turns live outside the question budget."""
    return str(key or "").startswith(("clarify:", "adaptive:", "schema:"))
