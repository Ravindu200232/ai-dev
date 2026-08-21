"""Small generation policies shared by build and edit agents."""
from __future__ import annotations

import re


HUMAN_COMMENT_POLICY = """\
SOURCE COMMENT STYLE:
- Write comments only when they explain WHY, a non-obvious invariant, or a risky boundary.
- Keep comments short and natural, like a careful maintainer wrote them.
- Do not narrate obvious JSX, imports, assignments, or every function line by line.
- Never mention the AI/model/prompt, and do not leave fake TODOs or tutorial commentary.
- Prefer clear names and small functions over comments that explain confusing code.
"""


FEATURE_IMAGE_POLICY = """\
FEATURE IMAGE CONTRACT:
- This request explicitly needs a generated picture/photo/visual asset.
- Reference each new generated asset from app source as /generated/<semantic-name>.png.
- Give image elements useful, concrete alt text; that text becomes the Fooocus subject prompt.
- Do not use remote stock URLs, placeholder services, base64 blobs, or invented existing files.
- AgentForge generates any missing /generated/*.png reference with Fooocus after the source write.
"""


_IMAGE_INTENT_RE = re.compile(
    r"\b(?:image|images|photo|photos|picture|pictures|photograph|photographs|"
    r"banner\s+image|hero\s+image|background\s+image|thumbnail|cover\s+image|"
    r"illustration|visual\s+asset)\b",
    re.I,
)
_IMAGE_NEGATION_RE = re.compile(
    r"\b(?:no|without|remove|delete|hide|disable)\b[^.!;\n]{0,40}"
    r"\b(?:image|images|photo|photos|picture|pictures|thumbnail|illustration)s?\b",
    re.I,
)
_IMAGE_ADD_RE = re.compile(
    r"\b(?:add|include|generate|create|draw|use|put|insert|replace)\b[^.!;\n]{0,40}"
    r"\b(?:image|images|photo|photos|picture|pictures|thumbnail|illustration)s?\b",
    re.I,
)


def feature_image_requested(text: str) -> bool:
    """True when a feature explicitly asks to add/use generated visual media."""
    value = " ".join(str(text or "").split())
    if not value:
        return False
    if _IMAGE_NEGATION_RE.search(value) and not _IMAGE_ADD_RE.search(value):
        return False
    return bool(_IMAGE_INTENT_RE.search(value))


def feature_image_prompt(text: str) -> str:
    """Return the extra feature contract only when visual generation was asked for."""
    return FEATURE_IMAGE_POLICY if feature_image_requested(text) else ""


__all__ = [
    "FEATURE_IMAGE_POLICY",
    "HUMAN_COMMENT_POLICY",
    "feature_image_prompt",
    "feature_image_requested",
]
