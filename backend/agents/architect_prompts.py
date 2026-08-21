"""Prompt registry for ArchitectAgent."""
from .architect_stack_rules import WRITE_FILE_TOOL, VITE_STACK_RULES, NEXT_STACK_RULES
from .architect_vite_prompts import VITE_PLANNER_SYSTEM, VITE_BUILDER_SYSTEM
from .architect_next_planner_prompt import NEXT_PLANNER_SYSTEM
from .architect_next_builder_prompt import NEXT_BUILDER_SYSTEM

PROMPTS = {
    "vite": {
        "rules":   VITE_STACK_RULES,
        "planner": VITE_PLANNER_SYSTEM,
        "builder": VITE_BUILDER_SYSTEM,
        "roots":   ("src/",),
        "entry":   ("src/App.jsx", "src/App.js"),
    },
    "next": {
        "rules":   NEXT_STACK_RULES,
        "planner": NEXT_PLANNER_SYSTEM,
        "builder": NEXT_BUILDER_SYSTEM,
        "roots":   ("app/", "components/", "lib/"),
        "entry":   ("app/page.js", "app/page.jsx"),
    },
}
