"""Validate, build and render diagram artifacts."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Callable

from ..config import settings
from ..services import storage
from .diagram_sources_core import (
    activity_diagram, erd_diagram, sequence_diagram, use_case_diagram,
)
from .diagram_sources_arch import (
    _BUILDERS, _first_lifecycle, _state_transitions, bpmn_diagram,
    class_object_diagram, component_diagram, deployment_diagram, dfd_diagram,
    state_machine_diagram, system_context_diagram,
)
from .diagram_sources_core import _plan, _san

STANDARD_DIAGRAM_KINDS = {"use_case", "sequence", "erd", "activity", "class_object",
                          "state_machine", "dfd", "bpmn"}
# Use the native renderer for customer diagrams.
DIAGRAM_STANDARD = {
    "use_case": "OMG UML 2.5.1", "sequence": "OMG UML 2.5.1",
    "activity": "OMG UML 2.5.1", "class_object": "OMG UML 2.5.1",
    "state_machine": "OMG UML 2.5.1", "bpmn": "OMG BPMN 2.0.2",
    "erd": "Crow's Foot ERD", "dfd": "Yourdon/DeMarco-style DFD",
    "system_context": "Architecture context view", "component": "UML-inspired component view",
    "deployment": "UML-inspired deployment view",
}
DIAGRAM_KINDS = [b[0] for b in _BUILDERS]
NATIVE_DIAGRAM_KINDS = set(DIAGRAM_KINDS)
DIAGRAM_TITLES = {b[0]: b[1] for b in _BUILDERS}


_SUBGRAPH = re.compile(r"^subgraph\s+([A-Za-z][\w]*)")


_DECL = re.compile(r"\b([A-Za-z][\w]*)\s*(?:\[\(§\)\]|\(\[§\]\)|\[§\]|\(§\)|\{§\})")
_ARROW = re.compile(r"\s*(?:-{2,}>|={2,}>|-\.-+>|-{3,}|={3,})\s*")
_IDENT = re.compile(r"^[A-Za-z][\w]*$")
_SKIP = ("classDef", "class ", "%%", "direction", "flowchart", "graph", "style", "linkStyle")


def _flowchart_problems(source: str) -> list[str]:
    nodes: set[str] = set()
    containers: set[str] = set()
    endpoints: list[str] = []
    opens = closes = 0

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.count('"') % 2:
            return [f"unbalanced quotes: {line[:60]}"]
        if line == "end":
            closes += 1
            continue
        m = _SUBGRAPH.match(line)
        if m:
            opens += 1
            containers.add(m.group(1))
            continue
        if line.startswith(_SKIP):
            continue

        s = re.sub(r'"[^"]*"', "§", line)
        s = re.sub(r"\|[^|]*\|", " ", s)
        s = re.sub(r":::\w+", "", s)
        s = re.sub(r"--\s*[^->]+?\s*-->", " --> ", s)

        for d in _DECL.finditer(s):
            nodes.add(d.group(1))
        s = _DECL.sub(r"\1", s)

        parts = [p.strip() for p in _ARROW.split(s)]
        if len(parts) > 1:
            for p in parts:
                if _IDENT.match(p):
                    endpoints.append(p)
                    nodes.add(p) if p not in containers else None

    problems: list[str] = []
    if opens != closes:
        problems.append(f"{opens} subgraph vs {closes} end")
    if len(nodes) < 2:
        problems.append(f"only {len(nodes)} nodes")
    if not endpoints:
        problems.append("no edges")
    linked = set(endpoints)
    orphans = sorted(nodes - linked)
    if orphans:
        problems.append("unconnected node(s): " + ", ".join(orphans[:5]))
    return problems


def mermaid_problems(kind: str, source: str) -> list[str]:
    """Everything structurally wrong with this diagram."""
    src = (source or "").strip()
    if len(src) < 30:
        return ["source is empty or a stub"]
    head = src.splitlines()[0].strip().lower()

    if kind == "erd":
        if not head.startswith("erdiagram"):
            return ["must start with erDiagram"]

        opens = len(re.findall(r"^\s*\w+\s*\{\s*$", src, re.M))
        closes = len(re.findall(r"^\s*\}\s*$", src, re.M))
        if opens != closes:
            return [f"{opens} entity blocks opened, {closes} closed"]
        return [] if opens else ["no entities"]

    if kind == "class_object":
        if not head.startswith("classdiagram"):
            return ["must start with classDiagram"]
        return [] if re.search(r"^\s*class\s+\w+", src, re.M) else ["no classes"]

    if kind == "state_machine":
        if not head.startswith("statediagram"):
            return ["must start with stateDiagram-v2"]
        return [] if "[*]" in src else ["missing initial/final pseudo-state"]

    if kind == "sequence":
        if not head.startswith("sequencediagram"):
            return ["must start with sequenceDiagram"]
        problems = []
        acts = len(re.findall(r"^\s*activate\s", src, re.M))
        deacts = len(re.findall(r"^\s*deactivate\s", src, re.M))
        if acts != deacts:
            problems.append(f"{acts} activate vs {deacts} deactivate")
        if len(re.findall(r"^\s*(?:actor|participant)\s", src, re.M)) < 2:
            problems.append("fewer than two participants")
        declared = set(re.findall(r"^\s*(?:actor|participant)\s+(\w+)", src, re.M))
        used = set(re.findall(r"^\s*(\w+)\s*-[->]+>?", src, re.M))
        used |= set(re.findall(r"-[->]+>?\s*(\w+)\s*:", src, re.M))
        unknown = sorted(used - declared - {"alt", "else", "end", "loop", "opt"})
        if unknown:
            problems.append("undeclared participant(s): " + ", ".join(unknown[:5]))
        return problems

    if not (head.startswith("flowchart") or head.startswith("graph")):
        return ["must start with flowchart or graph"]
    return _flowchart_problems(src)


def valid_mermaid(kind: str, source: str) -> bool:
    return not mermaid_problems(kind, source)


def diagram_applicability(kind: str, doc: dict) -> tuple[bool, str]:
    """Whether a diagram has enough SRS semantics to be meaningful."""
    if kind == "state_machine":
        life = _first_lifecycle(doc)
        transitions = _state_transitions(doc, life) if life else []
        if not life or not transitions:
            return False, "No explicit legal state-to-state transitions are specified in the SRS."
    if kind == "erd" and not ((doc.get("database_design") or {}).get("tables") or []):
        return False, "No persistent entities are specified in the SRS data model."
    if kind in {"activity", "bpmn", "sequence"} and not (doc.get("business_workflows") or []):
        plan = _plan(doc)
        if not (plan.get("workflows") or plan.get("features")):
            return False, "No ordered workflow or interaction is specified in the approved requirements."
    return True, ""


def build_diagrams(srs: dict, on_error: Callable[[str], None] | None = None) -> list[dict]:
    """Return fixed diagram artifacts (with Mermaid source)."""
    doc = srs.get("srs_document", srs)
    out: list[dict] = []
    for kind, title, fn in _BUILDERS:
        try:
            source = fn(doc)
        except Exception as exc:  # noqa: BLE001 - never let one diagram break the set
            if on_error:
                on_error(f"{kind}: builder raised {type(exc).__name__}: {exc}")
            source = (f'flowchart TD\n  err["{_san(f"{kind} could not be drawn")}"]\n'
                      f'  detail["{_san(str(exc), 80)}"]\n  err --> detail')
        else:

            problems = mermaid_problems(kind, source)
            if problems and on_error:
                on_error(f"{kind}: template produced invalid Mermaid — {'; '.join(problems)}")
        applicable, reason = diagram_applicability(kind, doc)
        out.append({"id": f"dia_{kind}", "kind": kind, "title": title,
                    "format": "mermaid", "source": source,
                    "standard": DIAGRAM_STANDARD.get(kind, ""),
                    "applicable": applicable, "applicability_note": reason,
                    "canonical_rendering": "native_svg"})
    return out


def build_one(kind: str, srs: dict) -> dict:
    """Deterministic single-diagram fallback by kind."""
    doc = srs.get("srs_document", srs)
    fn = dict((b[0], b[2]) for b in _BUILDERS).get(kind)
    src = fn(doc) if fn else f'flowchart TD\n  a["{kind}"]\n  b["unavailable"]\n  a --> b'
    applicable, reason = diagram_applicability(kind, doc)
    return {"id": f"dia_{kind}", "kind": kind, "title": DIAGRAM_TITLES.get(kind, kind),
            "format": "mermaid", "source": src, "standard": DIAGRAM_STANDARD.get(kind, ""),
            "applicable": applicable, "applicability_note": reason,
            "canonical_rendering": "native_svg"}


_UNSET = object()
_RENDERER_CACHE: object = _UNSET


def _works(cmd: list[str]) -> bool:
    """Does this command actually run?"""
    try:
        return subprocess.run(cmd + ["--version"], capture_output=True,
                              timeout=120).returncode == 0
    except Exception:  # noqa: BLE001 - not on PATH, not executable, too slow
        return False


def _find_renderer(refresh: bool = False) -> list[str] | None:
    """The first mermaid-cli invocation that actually works, or None."""
    global _RENDERER_CACHE
    if not refresh and _RENDERER_CACHE is not _UNSET:
        return _RENDERER_CACHE  # type: ignore[return-value]

    cli = (settings.mermaid_cli or "").strip()
    if cli.lower() in ("", "off", "none", "disabled", "__none__"):
        _RENDERER_CACHE = None
        return None

    candidates: list[list[str]] = []
    for name in (cli, "mmdc"):
        exe = shutil.which(name) if name else None
        if exe and [exe] not in candidates:
            candidates.append([exe])
    npx = shutil.which("npx")
    if npx:
        candidates.append([npx, "-y", "@mermaid-js/mermaid-cli"])

    for cmd in candidates:
        if _works(cmd):
            _RENDERER_CACHE = cmd
            return cmd
    _RENDERER_CACHE = None
    return None


def render_diagrams(project_id: str, diagrams: list[dict], *, doc: dict | None = None,
                    on_error: Callable[[str], None] | None = None) -> list[dict]:
    """Write sources and render diagrams."""
    ddir = storage.diagrams_dir(project_id)
    renderer = _find_renderer()
    doc = doc or {}

    if NATIVE_DIAGRAM_KINDS and doc:
        try:
            from reportlab.graphics import renderSVG
            from .diagram_draw import render_native
        except Exception as exc:  # noqa: BLE001
            renderSVG = None
            render_native = None
            if on_error:
                on_error(f"Native standards renderer unavailable: {type(exc).__name__}: {exc}")
    else:
        renderSVG = None
        render_native = None

    for d in diagrams:
        mmd_path = ddir / f"{d['kind']}.mmd"
        mmd_path.write_text(d["source"], encoding="utf-8")
        d["mmd_path"] = str(mmd_path)

        if d.get("kind") in NATIVE_DIAGRAM_KINDS and renderSVG and render_native:
            try:
                drawing = render_native(d["kind"], doc, 720)
                if drawing is not None:
                    svg_path = ddir / f"{d['kind']}.svg"
                    renderSVG.drawToFile(drawing, str(svg_path))
                    if svg_path.exists():
                        d["svg_path"] = str(svg_path)
                        d["format"] = "svg+mermaid-source"
                        d["rendered_by"] = "native_vector_v4"
                        continue
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    on_error(f"{d['kind']}.svg native render failed: {type(exc).__name__}: {exc}")

        if not renderer:
            continue
        for ext in ("svg", "png"):
            out_path = ddir / f"{d['kind']}.{ext}"
            try:
                subprocess.run(
                    [*renderer, "-i", str(mmd_path), "-o", str(out_path), "-b", "white",
                     "-s", "2" if ext == "png" else "1"],
                    check=True, capture_output=True, timeout=180,
                )
                if out_path.exists():
                    d[f"{ext}_path"] = str(out_path)
                    d.setdefault("rendered_by", "mermaid_cli")
            except subprocess.CalledProcessError as exc:
                if on_error:
                    detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
                    on_error(f"{d['kind']}.{ext} render failed: {detail[-400:] or exc}")
                break
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    on_error(f"{d['kind']}.{ext} render failed: {type(exc).__name__}: {exc}")
                break
    return diagrams
