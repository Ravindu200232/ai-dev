"""One focused change to a project that already exists, on the forge loop."""
from __future__ import annotations

import logging

from .. import skills as skillkit
from ..compaction import summariser
from ..context import Conversation
from ..loop import AgentLoop
from ..modes import BUILD, PLAN
from ..plan import parse as parse_plan
from ..tools.read import read_file
from .tools import edit_registry

log = logging.getLogger("forge.edit")

FOCUS_LIMIT = 4          # files pre-loaded before the model asks for anything


class EditAgent:
    """The loop the feature, element and sketch edits all run on.

    Each caller keeps its own system prompt — that is where the domain
    knowledge lives. What they share is this: real file tools, a context that
    compacts, and the skill for whatever the task turns out to be about.
    """

    def __init__(self, arch, project_dir, model, *, on_event=None,
                 budget: int = 20_000, should_stop=None, writer=None):
        self.arch = arch
        self.project_dir = project_dir
        self.model = model
        self.on_event = on_event
        self.budget = budget
        self.should_stop = should_stop
        self.writer = writer

    def _focus(self, paths) -> str:
        """The files the caller already knows matter, read in up front."""
        wanted = [p for p in (paths or []) if p][:FOCUS_LIMIT]
        if not wanted:
            return ""
        seen = [read_file(self.project_dir, path) for path in wanted]
        return ("What you are changing, as it is now:\n\n"
                + "\n\n".join(seen))

    def _loop(self, role: str, task: str, mode, focus=(), capture=None,
              images=()) -> AgentLoop:
        registry = edit_registry(self.arch, self.project_dir,
                                 read_only=mode.read_only, capture=capture,
                                 writer=self.writer)
        convo = Conversation(
            system=mode.system_prompt(role, skillkit.for_task(task)),
            goal=task, budget=self.budget,
            goal_extra={"images": list(images)} if images else {})
        grounding = self._focus(focus)
        if grounding:
            convo.add("user", grounding)
        return AgentLoop(self.model, registry, convo, mode=mode,
                         on_event=self.on_event,
                         summarise=summariser(self.model.ask),
                         should_stop=self.should_stop)

    def run(self, role: str, task: str, *, focus=(), max_turns=None,
            capture=None, images=()):
        """Make the change. Returns the loop result, including files touched.

        Pass `capture` — a dict — when the caller must inspect the rewrite
        before it is allowed to happen; the writes land there instead.
        """
        loop = self._loop(role, task, BUILD, focus, capture, images)
        if max_turns:
            loop.max_turns = int(max_turns)
        result = loop.run()
        log.info(f"edit finished: {result.stopped}, {len(result.files)} file(s)")
        return result

    def session(self, role: str, goal: str, *, mode=BUILD, focus=(),
                capture=None, images=()):
        """A loop that keeps one conversation across several rounds.

        An analysis that converges over rounds wants the same window each
        time — pinned goal, compacted history — not a fresh conversation per
        round. Call `session(...)` once, then `loop.run(note)` per round with
        `""` for the first.
        """
        return self._loop(role, goal, mode, focus, capture, images)

    def plan(self, role: str, task: str, *, focus=()):
        """Work out what to change without changing anything."""
        result = self._loop(role, task, PLAN, focus).run()
        return parse_plan(result.text, brief=task)
