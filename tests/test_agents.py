from unittest.mock import patch

from backend.agents import build_review_graph
from backend.ai_client import PRReview, ReviewFinding


def _finding(agent_type: str) -> ReviewFinding:
    return ReviewFinding(
        agent_type=agent_type,
        severity="medium",
        file_path="app.py",
        line_number=1,
        comment=f"{agent_type} issue found",
        suggested_fix=None,
    )


def _initial_state(diff: str = "some diff") -> dict:
    return {
        "diff": diff,
        "security_findings": [],
        "bug_findings": [],
        "quality_findings": [],
        "optimization_findings": [],
        "all_findings": [],
    }


@patch("backend.agents.analyze_pr")
def test_graph_fans_out_to_all_four_agents(mock_analyze):
    """
    All four agent nodes should run off the same diff, each with its own
    agent_type, and the synthesizer should wait for all of them before merging.
    """
    mock_analyze.side_effect = lambda diff, agent_type: PRReview(
        findings=[_finding(agent_type)], summary="ok"
    )

    graph = build_review_graph()
    result = graph.invoke(_initial_state())

    assert mock_analyze.call_count == 4
    called_agent_types = {call.args[1] for call in mock_analyze.call_args_list}
    assert called_agent_types == {"security", "bug", "quality", "optimization"}


@patch("backend.agents.analyze_pr")
def test_synthesize_merges_findings_from_every_agent(mock_analyze):
    mock_analyze.side_effect = lambda diff, agent_type: PRReview(
        findings=[_finding(agent_type)], summary="ok"
    )

    graph = build_review_graph()
    result = graph.invoke(_initial_state())

    all_comments = {f.comment for f in result["all_findings"]}
    assert all_comments == {
        "security issue found",
        "bug issue found",
        "quality issue found",
        "optimization issue found",
    }
    assert len(result["all_findings"]) == 4


@patch("backend.agents.analyze_pr")
def test_graph_handles_clean_diff_with_no_findings(mock_analyze):
    mock_analyze.return_value = PRReview(findings=[], summary="no issues found")

    graph = build_review_graph()
    result = graph.invoke(_initial_state())

    assert result["all_findings"] == []
