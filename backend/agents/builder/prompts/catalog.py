"""Prompt registry for the Next.js builder agent."""
from .stack import WRITE_FILE_TOOL, NEXT_STACK_RULES
from agents.planner.prompt import NEXT_PLANNER_SYSTEM
from .builder import NEXT_BUILDER_SYSTEM

PROMPTS = {
    "next": {
        "rules":   NEXT_STACK_RULES,
        "planner": NEXT_PLANNER_SYSTEM,
        "builder": NEXT_BUILDER_SYSTEM,
        "roots":   ("app/", "components/", "lib/"),
        "entry":   ("app/page.js", "app/page.jsx"),
    },
}
