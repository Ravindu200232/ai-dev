"""JavaScript syntax validation helpers."""
from .exports_common import *

_SYNTAX_SCRIPT = r"""
const fs = require('fs');
const path = require('path');
const esbuild = require(process.argv[2]);
const files = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = [];
for (const rel of files) {
  let src;
  try { src = fs.readFileSync(path.join(process.argv[4], rel), 'utf8'); }
  catch (e) { continue; }
  try {
    // `jsx` for .js too: Next lets a .js file contain JSX and the generated
    // apps use both extensions for components.
    esbuild.transformSync(src, { loader: 'jsx', sourcefile: rel });
  } catch (e) {
    const first = (e.errors && e.errors[0]) || {};
    out.push({
      path: rel,
      line: (first.location && first.location.line) || 0,
      message: first.text || String(e.message || e),
    });
  }
}
process.stdout.write(JSON.stringify(out));
"""


def check_syntax(project_dir, files, node_cmd=None) -> tuple[list, str]:
    """Every generated file that is not parseable JavaScript."""
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    root = Path(project_dir).resolve()
    esbuild_dir = root / "node_modules" / "esbuild"
    if not esbuild_dir.exists():
        return [], "esbuild is not installed in this project"

    node = node_cmd or shutil.which("node")
    if not node:
        return [], "node is not on PATH"

    targets = sorted(
        rel for rel in files
        if rel.endswith((".js", ".jsx")) and isinstance(files.get(rel), str)
        and not rel.startswith(("node_modules/", ".next/"))

        and not rel.endswith((".config.js", ".config.mjs"))
    )
    if not targets:
        return [], ""

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "parse.js"
        listing = Path(tmp) / "files.json"
        script.write_text(_SYNTAX_SCRIPT, encoding="utf-8")
        listing.write_text(json.dumps(targets), encoding="utf-8")
        try:
            done = subprocess.run(
                [node, str(script), str(esbuild_dir), str(listing), str(root)],
                capture_output=True, text=True, timeout=120)
        except Exception as exc:                        # noqa: BLE001
            return [], f"the parser could not be run ({exc})"

    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()
        return [], f"the parser failed: {detail[-1] if detail else 'no output'}"
    try:
        return json.loads(done.stdout or "[]"), ""
    except ValueError:
        return [], "the parser returned nothing readable"


def syntax_messages(problems: list) -> list:
    """One repair-ready sentence per unparseable file."""
    return [
        f"{p['path']}:{p.get('line') or 0}: {p['message']} — this file is not "
        f"valid JavaScript and nothing that imports it can compile. Fix the "
        f"syntax without changing what the file does."
        for p in problems
    ]

__all__ = [name for name in globals() if not name.startswith("__")]
