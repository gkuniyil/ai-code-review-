from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from backend.ai_client import ReviewFinding
from backend.tasks import _review_pr_async, embed_findings, recall_similar_findings

DIFF = (
    "\n--- app.py ---\n"
    "@@ -0,0 +1,3 @@\n"
    "+line one\n"
    "+line two\n"
    "+line three\n"
)


@pytest.fixture(autouse=True)
def fake_embedding_model():
    """keeps every task test from loading the real Hugging Face model"""
    with patch("backend.tasks.embed", side_effect=lambda texts: np.ones((len(texts), 384), dtype="float32")) as mock:
        yield mock


def _finding(line_number, file_path="app.py") -> ReviewFinding:
    return ReviewFinding(
        agent_type="bug",
        severity="high",
        file_path=file_path,
        line_number=line_number,
        comment=f"problem on line {line_number} of {file_path}",
        suggested_fix="fix it",
    )


def _pr(**overrides):
    fields = dict(
        id=99, repo_name="octocat/hello-world", github_pr_number=42, diff="",
        status="pending", review_started_at=None, completed_at=None, github_review_id=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _mock_session(pr):
    """
    Stands in for AsyncSessionLocal() — returns a session whose execute()
    always yields the given PR, so the task runs without a real Postgres.
    """
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = pr
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return session_factory, db


def _mock_task(retries=0):
    task = MagicMock()
    task.request.retries = retries
    task.max_retries = 3
    task.retry.side_effect = RuntimeError("retry scheduled")
    return task


@pytest.mark.asyncio
@patch("backend.tasks.post_review", return_value=555)
@patch("backend.tasks.review_graph")
@patch("backend.tasks.get_latest_commit_sha", return_value="sha123")
@patch("backend.tasks.fetch_pr_diff", return_value=DIFF)
async def test_findings_outside_the_diff_go_to_summary_not_inline(_diff, _sha, mock_graph, mock_post):
    """
    Regression test: an LLM-invented line number used to be posted as an inline
    comment, github rejected it with 422, and the whole review failed.
    """
    pr = _pr()
    session_factory, _ = _mock_session(pr)
    mock_graph.invoke.return_value = {"all_findings": [
        _finding(2),                          # valid: line 2 is in the diff
        _finding(500),                        # invalid: line not in the diff
        _finding(1, file_path="other.py"),    # invalid: file not in the diff
        _finding(None),                       # no line at all
    ]}

    with patch("backend.tasks.AsyncSessionLocal", session_factory):
        await _review_pr_async(_mock_task(), pr.id)

    mock_post.assert_called_once()
    repo, pr_number, sha, summary, comments = mock_post.call_args.args
    assert sha == "sha123"
    assert [(c["path"], c["line"]) for c in comments] == [("app.py", 2)]
    assert "Found 4 issues" in summary          # every finding still reported in the summary
    assert pr.github_review_id == 555
    assert pr.status == "completed"


@pytest.mark.asyncio
@patch("backend.tasks.post_review")
@patch("backend.tasks.review_graph")
@patch("backend.tasks.fetch_pr_diff")
async def test_retry_after_review_was_posted_does_not_post_again(mock_diff, mock_graph, mock_post):
    """
    If an earlier attempt already posted the review (github_review_id is set),
    a retry must not re-run the agents or post a second copy to the PR.
    """
    pr = _pr(github_review_id=555, status="retrying")
    session_factory, _ = _mock_session(pr)

    with patch("backend.tasks.AsyncSessionLocal", session_factory):
        await _review_pr_async(_mock_task(retries=1), pr.id)

    mock_diff.assert_not_called()
    mock_graph.invoke.assert_not_called()
    mock_post.assert_not_called()
    assert pr.status == "completed"


@pytest.mark.asyncio
@patch("backend.tasks.post_review", side_effect=Exception("github is down"))
@patch("backend.tasks.review_graph")
@patch("backend.tasks.get_latest_commit_sha", return_value="sha123")
@patch("backend.tasks.fetch_pr_diff", return_value=DIFF)
async def test_failure_with_retries_left_is_marked_retrying(_diff, _sha, mock_graph, _post):
    pr = _pr()
    session_factory, db = _mock_session(pr)
    mock_graph.invoke.return_value = {"all_findings": [_finding(2)]}
    task = _mock_task(retries=0)

    with patch("backend.tasks.AsyncSessionLocal", session_factory):
        with pytest.raises(RuntimeError, match="retry scheduled"):
            await _review_pr_async(task, pr.id)

    assert pr.status == "retrying"
    assert pr.github_review_id is None
    db.rollback.assert_called_once()
    task.retry.assert_called_once()


@pytest.mark.asyncio
@patch("backend.tasks.post_review", return_value=555)
@patch("backend.tasks.review_graph")
@patch("backend.tasks.get_latest_commit_sha", return_value="sha123")
@patch("backend.tasks.fetch_pr_diff", return_value=DIFF)
@patch("backend.tasks.recall_similar_findings", new_callable=AsyncMock)
async def test_similar_past_findings_are_passed_to_the_agents(mock_recall, _diff, _sha, mock_graph, _post):
    hint = {"agent_type": "bug", "severity": "high", "file_path": "app.py", "comment": "off by one", "similarity": 0.6}
    mock_recall.return_value = [hint]
    mock_graph.invoke.return_value = {"all_findings": []}
    pr = _pr()
    session_factory, _ = _mock_session(pr)

    with patch("backend.tasks.AsyncSessionLocal", session_factory):
        await _review_pr_async(_mock_task(), pr.id)

    assert mock_graph.invoke.call_args.args[0]["similar_findings"] == [hint]


@pytest.mark.asyncio
@patch("backend.tasks.post_review", return_value=555)
@patch("backend.tasks.review_graph")
@patch("backend.tasks.get_latest_commit_sha", return_value="sha123")
@patch("backend.tasks.fetch_pr_diff", return_value=DIFF)
async def test_findings_are_saved_with_code_snippet_and_embedding(_diff, _sha, mock_graph, _post):
    mock_graph.invoke.return_value = {"all_findings": [_finding(2), _finding(500)]}
    pr = _pr()
    session_factory, db = _mock_session(pr)

    with patch("backend.tasks.AsyncSessionLocal", session_factory):
        await _review_pr_async(_mock_task(), pr.id)

    saved = {r.line_number: r for r in (call.args[0] for call in db.add.call_args_list)}
    assert saved[2].code_snippet == "line one\nline two\nline three"
    assert len(saved[2].embedding) == 384 * 4
    assert saved[500].code_snippet is None     # line not in the diff → nothing to remember
    assert saved[500].embedding is None


@pytest.mark.asyncio
async def test_memory_lookup_failure_returns_no_hints_instead_of_failing():
    db = MagicMock()
    db.execute = AsyncMock(side_effect=Exception("db hiccup"))

    assert await recall_similar_findings(db, _pr(), DIFF) == []


def test_embedding_failure_saves_findings_without_embeddings(fake_embedding_model):
    fake_embedding_model.side_effect = RuntimeError("model failed to load")

    assert embed_findings([_finding(2)], DIFF) == [("line one\nline two\nline three", None)]


@pytest.mark.asyncio
@patch("backend.tasks.fetch_pr_diff", side_effect=Exception("github is down"))
async def test_failure_on_last_attempt_is_marked_failed(_diff):
    pr = _pr()
    session_factory, _ = _mock_session(pr)

    with patch("backend.tasks.AsyncSessionLocal", session_factory):
        with pytest.raises(RuntimeError):
            await _review_pr_async(_mock_task(retries=3), pr.id)

    assert pr.status == "failed"
