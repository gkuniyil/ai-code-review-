"""
tests for the read-only queries behind the MCP server, run against a real
(in-memory SQLite) database so the actual SQL — joins, filters, grouping — is exercised.
"""
from unittest.mock import patch

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend import embeddings, review_queries
from backend.database import Base
from backend.embeddings import to_bytes
from backend.models import PullRequest, Review


def _vec(*hot):
    """unit vector with 1s in the given positions — stands in for a real embedding"""
    v = np.zeros(384, dtype="float32")
    v[list(hot)] = 1.0
    return v / np.linalg.norm(v)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        def pr(id, repo, number, delivery):
            return PullRequest(id=id, github_delivery_id=delivery, github_pr_number=number, repo_name=repo,
                               title=f"PR {number}", author="octocat", diff="", status="completed")

        def finding(pr_id, agent, severity, comment, embedding=None):
            return Review(pr_id=pr_id, agent_type=agent, severity=severity, file_path="app.py", line_number=1,
                          comment=comment, suggested_fix="fix", code_snippet="code" if embedding is not None else None,
                          embedding=to_bytes(embedding) if embedding is not None else None)

        session.add_all([
            pr(1, "octo/api", 7, "d1"),
            pr(2, "octo/api", 7, "d2"),        # second push to the same PR
            pr(3, "octo/web", 3, "d3"),
        ])
        await session.flush()
        session.add_all([
            finding(1, "security", "high", "SQL injection in query", _vec(0)),
            finding(1, "quality", "low", "unclear variable name", _vec(1)),
            finding(2, "security", "high", "hardcoded secret", _vec(2)),
            finding(3, "bug", "medium", "off by one in pagination", _vec(0)),   # other repo
        ])
        await session.commit()
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_list_review_runs_newest_first_with_finding_counts(db):
    runs = await review_queries.list_review_runs(db)

    assert [(r["pr_id"], r["finding_count"]) for r in runs] == [(3, 1), (2, 1), (1, 2)]


@pytest.mark.asyncio
async def test_list_review_runs_filters_by_repo_and_clamps_limit(db):
    runs = await review_queries.list_review_runs(db, repo="octo/api", limit=0)

    assert [r["pr_id"] for r in runs] == [2]     # limit 0 is clamped up to 1


@pytest.mark.asyncio
async def test_get_review_run_includes_findings(db):
    review = await review_queries.get_review_run(db, 1)

    assert review["repo"] == "octo/api"
    assert [f["comment"] for f in review["findings"]] == ["SQL injection in query", "unclear variable name"]


@pytest.mark.asyncio
async def test_get_review_run_unknown_id_returns_none(db):
    assert await review_queries.get_review_run(db, 999) is None


@pytest.mark.asyncio
async def test_list_findings_combines_filters(db):
    findings = await review_queries.list_findings(db, repo="octo/api", severity="high", agent_type="security")

    assert [f["comment"] for f in findings] == ["hardcoded secret", "SQL injection in query"]
    assert all(f["repo"] == "octo/api" and f["pr_number"] == 7 for f in findings)


@pytest.mark.asyncio
async def test_review_stats_per_repo_and_overall(db):
    api = await review_queries.review_stats(db, repo="octo/api")
    everything = await review_queries.review_stats(db)

    assert api["review_runs"] == 2
    assert api["total_findings"] == 3
    assert api["findings_by_severity"] == {"high": 2, "low": 1}
    assert api["findings_by_agent"] == {"security": 2, "quality": 1}
    assert everything["review_runs"] == 3
    assert everything["total_findings"] == 4


@pytest.mark.asyncio
async def test_find_similar_past_issues_searches_only_the_given_repo(db):
    # the query "code" embeds to the same direction as the SQL injection finding
    with patch.object(embeddings, "embed", return_value=np.stack([_vec(0)])):
        results = await review_queries.find_similar_past_issues(db, repo="octo/api", code="SELECT ...")

    # octo/web's finding has the identical vector but belongs to another repo
    assert [r["comment"] for r in results] == ["SQL injection in query"]
    assert results[0]["pr_number"] == 7
    assert "embedding" not in results[0]
