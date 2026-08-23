"""Forge — a small agent core: tools, a context budget, plan mode, skills.

The pieces are meant to be readable on their own:

    tools/       what the model may call, and the sandbox it calls it in
    context.py   the window: what is pinned, what is recent
    compaction.py how a long run stays inside that window
    modes.py     plan mode (read-only) and build mode (writes)
    plan.py      the plan a human approves before anything is written
    loop.py      ask, run what it asked for, hand back what happened
    skills/      short guides loaded only when a task needs them
    builder/     planning and building a Next.js application
    qa/          Vitest and Playwright, run and repaired
    service.py   the pipeline that puts those in order
"""
from .context import Conversation
from .llm import Model, ScriptedModel
from .loop import AgentLoop, LoopResult
from .modes import BUILD, PLAN, Mode, mode_for
from .plan import Plan, parse as parse_plan
from .service import Pipeline, PipelineResult
from .tools import ToolRegistry, build_registry

__all__ = ["AgentLoop", "BUILD", "Conversation", "LoopResult", "Mode", "Model",
           "PLAN", "Pipeline", "PipelineResult", "Plan", "ScriptedModel",
           "ToolRegistry", "build_registry", "mode_for", "parse_plan"]
