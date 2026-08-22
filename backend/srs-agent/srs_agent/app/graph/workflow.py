"""LangGraph assembly."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..agents.coverage_auditor import audit_node
from ..agents.customization import customize_node
from ..agents.diagram_generator import diagram_node
from ..agents.domain_classifier import classify_node
from ..agents.english_plan import english_plan_node
from ..agents.intake import intake_node
from ..agents.pdf_generator import generate_pdf
from ..agents.srs_generator import generate_srs_node
from ..agents.state import AgentState
from ..services.events import bus

__all__ = ["run_analysis", "run_generation", "run_customization", "generate_pdf"]


async def clarify_node(state: AgentState) -> AgentState:
    await bus.emit(state["project_id"], "InterviewAgent",
                   "The idea was hard to read — starting from the app type.",
                   level="warn", progress=60)
    return {**state, "needs_clarification": True}


def _route_after_intake(state: AgentState) -> str:
    return "clarify" if state.get("is_nonsense") else "classify"


def _build_analysis():
    g = StateGraph(AgentState)
    g.add_node("intake", intake_node)
    g.add_node("classify", classify_node)
    g.add_node("clarify", clarify_node)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _route_after_intake,
                            {"clarify": "clarify", "classify": "classify"})
    g.add_edge("classify", END)
    g.add_edge("clarify", END)
    return g.compile()


def _build_generation():
    g = StateGraph(AgentState)
    g.add_node("audit", audit_node)
    g.add_node("english_plan", english_plan_node)
    g.add_node("generate", generate_srs_node)
    g.add_node("render_diagrams", diagram_node)
    g.add_edge(START, "audit")
    g.add_edge("audit", "english_plan")
    g.add_edge("english_plan", "generate")
    g.add_edge("generate", "render_diagrams")
    g.add_edge("render_diagrams", END)
    return g.compile()


def _build_customization():
    g = StateGraph(AgentState)
    g.add_node("customize", customize_node)
    g.add_node("render_diagrams", diagram_node)
    g.add_edge(START, "customize")
    g.add_edge("customize", "render_diagrams")
    g.add_edge("render_diagrams", END)
    return g.compile()


_analysis = None
_generation = None
_customization = None


async def run_analysis(state: AgentState) -> AgentState:
    global _analysis
    if _analysis is None:
        _analysis = _build_analysis()
    return await _analysis.ainvoke(state)


async def run_generation(state: AgentState) -> AgentState:
    global _generation
    if _generation is None:
        _generation = _build_generation()
    return await _generation.ainvoke(state)


async def run_customization(state: AgentState) -> AgentState:
    global _customization
    if _customization is None:
        _customization = _build_customization()
    return await _customization.ainvoke(state)
