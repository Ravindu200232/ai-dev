"""Next.js builder system prompt."""
from .architect_next_builder_prompt_a import PROMPT_PART_A
from .architect_next_builder_prompt_b import PROMPT_PART_B

NEXT_BUILDER_SYSTEM = PROMPT_PART_A + PROMPT_PART_B
