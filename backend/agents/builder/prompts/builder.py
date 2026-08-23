"""Combined Next.js builder system prompt."""
from .part_a import PROMPT_PART_A
from .part_b import PROMPT_PART_B

NEXT_BUILDER_SYSTEM = PROMPT_PART_A + PROMPT_PART_B
