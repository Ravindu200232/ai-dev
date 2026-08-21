"""Legacy BuilderAgent wrapper."""
from .builder_common import *
from .builder_generation import BuilderGenerationMixin
from .builder_templates import BuilderTemplateMixin
from .builder_sanitize import BuilderSanitizeMixin

class BuilderAgent(BuilderGenerationMixin, BuilderTemplateMixin, BuilderSanitizeMixin):
    pass


# Preserve the public surface of the old module.
__all__ = ['BuilderAgent', 'SAFE_COMPONENT', 'SYSTEM_PROMPT', 'log', 'set_stream_callback']
