"""Post-build analyzer wrapper built from mixins."""
from .analyzer_common import *
from .analyzer_context import AnalyzerContextMixin
from .analyzer_data import AnalyzerDataMixin
from .analyzer_code import AnalyzerCodeMixin
from .analyzer_ui import AnalyzerUIMixin
from .analyzer_workflows import AnalyzerWorkflowMixin
from .analyzer_runtime import AnalyzerRuntimeMixin
from .analyzer_repair import AnalyzerRepairMixin


class AnalyzerAgent(
    AnalyzerContextMixin, AnalyzerDataMixin, AnalyzerCodeMixin, AnalyzerUIMixin,
    AnalyzerWorkflowMixin, AnalyzerRuntimeMixin, AnalyzerRepairMixin,
):
    """Read a finished project back and compare it against its accepted plan."""
    pass



# Preserve the public surface of the old module.
__all__ = ['AnalyzerAgent', 'AnalyzerReport', 'BCRYPT_LITERAL_RE', 'CODE_EXT', 'FETCH_URL_RE', 'Finding', 'HTTP_METHOD_RE', 'LINK_HREF_RE', 'MAX_FILE_BYTES', 'NEXT_ROOTS', 'PLACEHOLDER_RE', 'PROSE_PATH_RE', 'ROOT_SOURCE', 'ROUTER_PUSH_RE', 'SEVERITIES', 'SKIP_DIRS', 'SOURCE_EXT', 'log']
