"""The context window: what is pinned, what is recent, and how full it is."""
from __future__ import annotations

from dataclasses import dataclass, field

from .tokens import message_tokens, total_tokens

MAX_SUMMARIES = 3


@dataclass
class Conversation:
    """Messages for one task, with the two that must never be dropped pinned.

    `system` is the role the agent is playing and `goal` is what it was asked
    to do. Everything else is working history and may be summarised away.
    """

    system: str = ""
    goal: str = ""
    turns: list = field(default_factory=list)
    goal_extra: dict = field(default_factory=dict)
    budget: int = 24_000
    summaries: list = field(default_factory=list)

    def add(self, role: str, content: str, **extra) -> dict:
        """Append one message and return it."""
        message = {"role": role, "content": str(content or "")}
        message.update({k: v for k, v in extra.items() if v})
        self.turns.append(message)
        return message

    def add_message(self, message: dict) -> dict:
        """Append a message the model produced, as-is."""
        self.turns.append(dict(message or {}))
        return self.turns[-1]

    def pinned(self) -> list:
        """System prompt, original goal, then any summary of what was dropped."""
        head = []
        if self.system:
            head.append({"role": "system", "content": self.system})
        if self.goal:
            head.append({"role": "user", "content": self.goal,
                         **{k: v for k, v in self.goal_extra.items() if v}})
        for summary in self.summaries:
            head.append({"role": "system",
                         "content": f"Summary of earlier work:\n{summary}"})
        return head

    def messages(self) -> list:
        """What actually gets sent."""
        return self.pinned() + list(self.turns)

    def tokens(self) -> int:
        return total_tokens(self.messages())

    def pinned_tokens(self) -> int:
        return total_tokens(self.pinned())

    def over_budget(self) -> bool:
        return self.tokens() > self.budget

    def pressure(self) -> float:
        """0.0 empty, 1.0 exactly at budget, above that means compact now."""
        return round(self.tokens() / max(1, self.budget), 3)

    def last_text(self) -> str:
        """The most recent assistant answer, which is usually the result."""
        for message in reversed(self.turns):
            if message.get("role") == "assistant" and message.get("content"):
                return str(message["content"])
        return ""

    def note(self, summary: str) -> None:
        """Pin a summary of history that is about to be dropped.

        Summaries roll: past `MAX_SUMMARIES`, the older ones are folded into
        one block with repeated lines removed, so a long run keeps one growing
        account of itself instead of a stack of near-identical notes.
        """
        text = str(summary or "").strip()
        if not text:
            return
        self.summaries.append(text)
        if len(self.summaries) > MAX_SUMMARIES:
            older = "\n".join(self.summaries[:-1]).splitlines()
            folded = list(dict.fromkeys(line for line in older if line.strip()))
            self.summaries = ["\n".join(folded), self.summaries[-1]]

    def stats(self) -> dict:
        """What a progress event wants to show about the window."""
        return {
            "tokens": self.tokens(),
            "budget": self.budget,
            "pressure": self.pressure(),
            "turns": len(self.turns),
            "summaries": len(self.summaries),
            "largest": max((message_tokens(m) for m in self.turns), default=0),
        }
