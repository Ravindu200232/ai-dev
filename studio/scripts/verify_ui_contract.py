"""Fast source checks for the premium Studio integration contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


checks = {
    "feature/bug approval": ("app/page.jsx", "setPendingAsk({ payload"),
    "prompt review modal": ("app/page.jsx", "<TunePrompt"),
    "agent sidebar removed": ("app/page.jsx", "<Pipeline"),
    "preview activity drawer": ("components/PreviewConsoleDrawer.jsx", "<Activity className="),
    "preview terminal drawer": ("components/PreviewConsoleDrawer.jsx", "<SquareTerminal className="),
    "friendly path mapper": ("lib/activity.js", "export function friendlyTarget"),
    "stable build stage": ("components/BuildOverlay.jsx", "const buildSeen ="),
    "repair does not rewind milestone": ("components/BuildOverlay.jsx", "stage: railStage, eyebrow: 'Repairing'"),
    "parallel testing state": ("components/BuildOverlay.jsx", "Parallel testing"),
    "no overlay traffic lights": ("components/BuildOverlay.jsx", "TrafficLights"),
    "preview drawer mounted": ("components/PreviewPane.jsx", "<PreviewConsoleDrawer"),
    "client route sync": ("components/PreviewPane.jsx", "syncPath('poll')"),
    "real E2E frame": ("components/PreviewPane.jsx", "Live Playwright browser"),
    "sequential E2E overlay": ("components/LiveE2EOverlay.jsx", "Live browser test"),
    "complete test suite stays visible": ("components/testing/TestingResult.jsx", "Previous {suite.total || 0} test cases remain below"),
    "assertion-derived counts": ("lib/test-counts.js", "assertionResults"),
    "SRS file intake": ("components/srs/Attachments.jsx", "PDF / image"),
    "SRS voice intake": ("components/srs/Attachments.jsx", "> Voice<"),
    "messenger SRS interview": ("components/srs/Interview.jsx", "srs-messenger"),
    "premium plan review": ("components/srs/PlanReview.jsx", "Product blueprint"),
    "premium design gallery": ("components/ThemePicker.jsx", "Choose the visual direction"),
    "planner model role": ("components/Sidebar.jsx", 'label="Planner"'),
    "design model role": ("components/Sidebar.jsx", 'label="Design"'),
    "builder model role": ("components/Sidebar.jsx", 'label="Builder"'),
    "builder-and-qa thinking": ("components/Sidebar.jsx", "Builder and QA work"),
    "short cloud model labels": ("lib/models.js", "'qwen3.5:397b-cloud': 'Qwen 397B'"),
    "selected model uses display label": ("components/ModelPicker.jsx", "current?.label || value"),
    "deployment workspace": ("components/deploy/DeployPanel.jsx", "Deploy"),
}

failed = []
for label, (path, needle) in checks.items():
    present = needle in text(path)
    if label in {"agent sidebar removed", "no overlay traffic lights"}:
        if present:
            failed.append(f"{label}: legacy marker {needle!r} still present in {path}")
    elif not present:
        failed.append(f"{label}: missing {needle!r} in {path}")

pipeline = ROOT / "components" / "Pipeline.jsx"
if pipeline.exists():
    failed.append("agent sidebar removed: components/Pipeline.jsx still exists")

legacy_builder_markers = {
    "components/Home.jsx": ("agentMode", "type: 'build'", "models.refine", "build_model"),
    "components/Sidebar.jsx": ("agentMode", 'label="Refine"', 'label="Build"'),
    "lib/store.js": ("agentMode", "agentforge-rm", "agentforge-bm"),
    "lib/api.js": ("build: '/build'", "update: '/update'"),
}
for path, markers in legacy_builder_markers.items():
    body = text(path)
    for marker in markers:
        if marker in body:
            failed.append(f"legacy Vite builder UI: {marker!r} still present in {path}")

if failed:
    raise SystemExit("\n".join(failed))
print(f"Studio UI contracts: {len(checks) + 1}/{len(checks) + 1} OK")
