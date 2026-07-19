from typing import TypedDict, List, Annotated
import operator
from langgraph.graph import StateGraph, END
from backend.ai_client import analyze_pr, ReviewFinding


class AgentState(TypedDict):
    """
    shared state passed between every node in the graph.
    """
    diff: str                                                            # PR code changes
    security_findings: Annotated[List[ReviewFinding], operator.add]     # security agent output
    bug_findings: Annotated[List[ReviewFinding], operator.add]          # bug agent output
    quality_findings: Annotated[List[ReviewFinding], operator.add]      # quality agent output
    optimization_findings: Annotated[List[ReviewFinding], operator.add] # optimization agent output
    all_findings: Annotated[List[ReviewFinding], operator.add]          # synthesizer output


def run_security_agent(state: AgentState) -> dict:
    """
    security node — reads diff, finds vulnerabilities.
    returns only security_findings — LangGraph merges into state automatically.
    """
    review = analyze_pr(state["diff"], "security")
    return {"security_findings": review.findings}


def run_bug_agent(state: AgentState) -> dict:
    """bug node — reads diff, finds logical errors and crashes"""
    review = analyze_pr(state["diff"], "bug")
    return {"bug_findings": review.findings}


def run_quality_agent(state: AgentState) -> dict:
    """quality node — reads diff, finds readability and style issues"""
    review = analyze_pr(state["diff"], "quality")
    return {"quality_findings": review.findings}


def run_optimization_agent(state: AgentState) -> dict:
    """optimization node — reads diff, finds performance issues"""
    review = analyze_pr(state["diff"], "optimization")
    return {"optimization_findings": review.findings}


def synthesize(state: AgentState) -> dict:
    """
    synthesis node — combines all agent findings into one list.
    runs AFTER all 4 agents finish — LangGraph waits for all parallel nodes.
    """
    all_findings = (
        state.get("security_findings", []) +
        state.get("bug_findings", []) +
        state.get("quality_findings", []) +
        state.get("optimization_findings", [])
    )
    return {"all_findings": all_findings}


def build_review_graph():
    """
    builds the LangGraph review graph.
    uses fan-out pattern: START connects to all 4 agents simultaneously.
    all 4 agents connect to synthesizer which waits for all to finish.
    """
    graph = StateGraph(AgentState)

    # add all nodes
    graph.add_node("security", run_security_agent)
    graph.add_node("bug", run_bug_agent)
    graph.add_node("quality", run_quality_agent)
    graph.add_node("optimization", run_optimization_agent)
    graph.add_node("synthesize", synthesize)

    # fan-out from START to all 4 agents simultaneously
    # this is how LangGraph runs nodes in parallel
    graph.set_entry_point("security")
    graph.add_edge("__start__", "bug")        # connect start to bug
    graph.add_edge("__start__", "quality")    # connect start to quality
    graph.add_edge("__start__", "optimization") # connect start to optimization

    # all agents connect to synthesizer
    graph.add_edge("security", "synthesize")
    graph.add_edge("bug", "synthesize")
    graph.add_edge("quality", "synthesize")
    graph.add_edge("optimization", "synthesize")

    # synthesizer connects to END
    graph.add_edge("synthesize", END)

    return graph.compile()


# compile once at module level — reused for every PR review
review_graph = build_review_graph()