"""The refine → build → test flow for one idea file."""
from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path

from agents.builder import BuilderAgent
from agents.refiner import RefinerAgent
from agents.tester import TesterAgent

from .config import (BUILD_MODEL, DEV_PORT, MAX_FIX, OLLAMA_URL, PROD_DIR,
                     REFINE_MODEL, log)
from .dev_server import start_vite


def write_readme(project_name: str, project_dir: Path, raw_idea: str) -> None:
    """Leave the generated project with the small run receipt users expect."""
    (Path(project_dir).parent / "README.md").write_text(
        f"# {project_name.replace('_', ' ').title()}\n\n"
        f"## Idea\n{raw_idea}\n\n"
        "## Run\n```bash\ncd src\nnpm install\nnpm run dev\n```\n",
        encoding="utf-8",
    )


def run_pipeline(idea_file: Path) -> None:
    """Process one idea file through the original three-agent pipeline."""
    idea_file = Path(idea_file)
    log.info("=" * 60)
    log.info("🚀 PIPELINE STARTED")
    log.info("=" * 60)
    raw_idea = idea_file.read_text(encoding="utf-8").strip()
    if not raw_idea:
        log.warning("Idea file is empty. Skipping.")
        return
    log.info(f"💡 Idea: {raw_idea[:200]}...")

    log.info(f"\n🧠 AGENT 1 — Refining with {REFINE_MODEL}...")
    refined = RefinerAgent(OLLAMA_URL, REFINE_MODEL).refine(raw_idea)
    if not refined:
        log.error("Refiner failed.")
        return
    log.info("✅ Refined OK")

    project_name = idea_file.stem.replace(" ", "_").lower()
    project_dir = PROD_DIR / project_name / "src"
    project_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"\n🏗️  AGENT 2 — Building with {BUILD_MODEL}...")
    builder = BuilderAgent(OLLAMA_URL, BUILD_MODEL, project_dir)
    if not builder.build(refined):
        log.error("Build failed.")
        return
    log.info(f"✅ Built at: {project_dir}")

    start_vite(project_dir)
    time.sleep(6)

    log.info("\n🧪 AGENT 3 — Testing...")
    tester = TesterAgent(project_dir, DEV_PORT)
    for attempt in range(1, MAX_FIX + 1):
        errors = tester.test()
        if not errors:
            log.info("✅ All tests passed!")
            break
        log.warning(f"⚠️  Attempt {attempt}/{MAX_FIX} — {len(errors)} issue(s):")
        for error in errors:
            log.warning(f"   • {error}")
        if attempt == MAX_FIX:
            log.warning("⚠️  Max attempts reached. Serving anyway.")
            break
        builder.fix(errors)

    write_readme(project_name, project_dir, raw_idea)

    def open_later() -> None:
        time.sleep(3)
        webbrowser.open(f"http://localhost:{DEV_PORT}")
        log.info(f"🖥️  Opened browser → http://localhost:{DEV_PORT}")

    threading.Thread(target=open_later, daemon=True, name="pipeline-browser").start()
    log.info("=" * 60)
    log.info(f"🎉 DONE!  http://localhost:{DEV_PORT}")
    log.info(f"   📁 Code: production-ready/{project_name}/src/")
    log.info("=" * 60)
