"""A query parameter the handler ignores, and authoring that reads first."""
from pathlib import Path
from types import SimpleNamespace

from agents.gates.agent import AnalyzerAgent
from agents.gates.analyzer_common import REPAIRABLE_MAJOR

ROOT = Path(__file__).resolve().parents[1]

CALLER = """'use client'
export default function Terminal() {
  async function lookUp(code) {
    const res = await fetch(`/api/products?barcode=${code}`)
    const item = await res.json()
    return item.sell_price.toFixed(2)
  }
  return <button onClick={() => lookUp('1')}>Add</button>
}
"""

IGNORING_HANDLER = """import { getCollection } from '@/lib/mongodb'
export async function GET() {
  const col = await getCollection('products')
  return Response.json(await col.find({}).toArray())
}
"""

READING_HANDLER = """import { getCollection } from '@/lib/mongodb'
export async function GET(request) {
  const barcode = new URL(request.url).searchParams.get('barcode')
  const col = await getCollection('products')
  if (barcode) return Response.json(await col.findOne({ barcode }))
  return Response.json(await col.find({}).toArray())
}
"""


def _project(tmp_path, handler):
    (tmp_path / "app" / "api" / "products").mkdir(parents=True)
    (tmp_path / "app" / "api" / "products" / "route.js").write_text(
        handler, encoding="utf-8")
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "Terminal.jsx").write_text(CALLER, encoding="utf-8")
    arch = SimpleNamespace(files={}, plan={}, plan_md="", write_seq=0)
    return AnalyzerAgent(arch, tmp_path)


def test_a_handler_that_ignores_its_query_parameter_is_a_finding(tmp_path):
    findings = _project(tmp_path, IGNORING_HANDLER).query_contract_findings()

    assert len(findings) == 1
    finding = findings[0]
    assert finding.code == "IGNORED_QUERY_PARAM"
    assert finding.severity == "major"
    # The repair belongs to the handler, and the caller is carried with it.
    assert finding.path == "app/api/products/route.js"
    assert "components/Terminal.jsx" in finding.extra
    assert "barcode" in finding.message
    assert "searchParams" in finding.fix


def test_a_handler_that_reads_the_parameter_is_not_a_finding(tmp_path):
    assert _project(tmp_path, READING_HANDLER).query_contract_findings() == []


def test_a_caller_with_no_query_string_is_not_a_finding(tmp_path):
    analyzer = _project(tmp_path, IGNORING_HANDLER)
    (tmp_path / "components" / "Terminal.jsx").write_text(
        "export default function T() { return fetch('/api/products') }",
        encoding="utf-8")
    analyzer._files_cache = None

    assert analyzer.query_contract_findings() == []


def test_a_query_against_a_url_no_route_serves_is_left_to_the_contract_check(tmp_path):
    analyzer = _project(tmp_path, IGNORING_HANDLER)
    (tmp_path / "components" / "Terminal.jsx").write_text(
        "export default function T() { return fetch('/api/nope?x=1') }",
        encoding="utf-8")
    analyzer._files_cache = None

    # BROKEN_CONTRACT already owns "there is no such route"; this check must
    # not invent a second, differently-worded finding for the same defect.
    assert analyzer.query_contract_findings() == []


def test_the_finding_is_repairable_and_reaches_every_pass_that_can_fix_it():
    assert "IGNORED_QUERY_PARAM" in REPAIRABLE_MAJOR

    scan = (ROOT / "agents" / "gates"
            / "analyzer_workflows.py").read_text(encoding="utf-8")
    assert "r.findings.extend(self.query_contract_findings(r.routes))" in scan

    # Same check before handoff, so the builder repairs it without a browser.
    rules = (ROOT / "agents" / "builder" / "workflow"
             / "rules.py").read_text(encoding="utf-8")
    lint = rules.split("def lint_plan_contracts")[1].split("\n    def ")[0]
    assert "az.query_contract_findings" in lint


def test_the_check_names_no_particular_app(tmp_path):
    source = (ROOT / "agents" / "gates"
              / "analyzer_context.py").read_text(encoding="utf-8")
    block = source.split("def query_contract_findings")[1].split("\n    def ")[0]
    for word in ("barcode", "sku", "product", "invoice", "tire"):
        assert word not in block.lower(), word


# ------------------------------------------------------- grounded authoring
def test_the_scenario_author_reads_the_app_before_it_writes_a_locator():
    source = (ROOT / "qa_agent" / "e2e_journeys.py").read_text(encoding="utf-8")
    author = source.split("    def author(")[1].split("\n    def ")[0]

    assert "workspace.schemas(E2E_AUTHOR_TOOLS)" in source
    assert "workspace.serve_calls(calls, names=E2E_AUTHOR_TOOLS)" in author
    assert "workspace.assistant_message(text, calls)" in author
    # One read round: authoring must not turn into its own agent loop.
    assert "E2E_AUTHOR_TOOL_ROUNDS = 1" in source


def test_the_author_offers_reads_only_never_writes_or_commands():
    from qa_agent.e2e_journeys import E2E_AUTHOR_TOOLS
    from agents.core.workspace import READ_TOOL_NAMES

    assert set(E2E_AUTHOR_TOOLS) <= set(READ_TOOL_NAMES)
    assert "run_command" not in E2E_AUTHOR_TOOLS
    assert "remember" not in E2E_AUTHOR_TOOLS


def test_the_runtime_bug_fixer_uses_the_standard_function_tools():
    source = (ROOT / "agents" / "repair" / "apply.py").read_text(encoding="utf-8")
    # Both fixers — the unit one and the E2E/runtime one — offer them now.
    assert source.count("tools=workspace.schemas(READ_TOOL_NAMES)") == 2
    assert source.count("workspace.serve_calls(") == 2
    # Every function call still gets answered, or the next turn is malformed.
    runtime = source.split("_e2e_privileged_paths = old_priv | set(")[1]
    assert "convo.extend(tool_messages)" in runtime
    assert "workspace.serve(reply)" in runtime          # old-host fallback kept
