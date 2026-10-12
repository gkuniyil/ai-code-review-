"""
review_queries.py

read-only queries over review history, used by the MCP server (mcp_tools.py).
kept separate from the MCP wrapper so they can be tested without the MCP SDK
(which needs newer starlette/pydantic than the FastAPI app pins).
"""
from sqlalchemy import select, func

from backend.models import PullRequest, Review
from backend.embeddings import MEMORY_SEARCH_LIMIT, find_similar, from_bytes

MAX_LIMIT = 100  # cap on rows per call — keeps responses small enough for an LLM's context


def _clamp(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _finding_dict(r: Review) -> dict:
    return {
        "id": r.id,
        "pr_id": r.pr_id,
        "agent_type": r.agent_type,
        "severity": r.severity,
        "file_path": r.file_path,
        "line_number": r.line_number,
        "comment": r.comment,
        "suggested_fix": r.suggested_fix,
    }


def _review_run_dict(pr: PullRequest, finding_count: int) -> dict:
    return {
        "pr_id": pr.id,
        "repo": pr.repo_name,
        "pr_number": pr.github_pr_number,
        "title": pr.title,
        "author": pr.author,
        "status": pr.status,
        "finding_count": finding_count,
        "created_at": pr.created_at.isoformat() if pr.created_at else None,
        "completed_at": pr.completed_at.isoformat() if pr.completed_at else None,
    }


async def list_review_runs(db, repo: str | None = None, limit: int = 10) -> list[dict]:
    """most recent review runs (one per webhook delivery), newest first, with finding counts"""
    query = (
        select(PullRequest, func.count(Review.id))
        .outerjoin(Review, Review.pr_id == PullRequest.id)
        .group_by(PullRequest.id)
        .order_by(PullRequest.id.desc())
        .limit(_clamp(limit))
    )
    if repo:
        query = query.where(PullRequest.repo_name == repo)
    rows = await db.execute(query)
    return [_review_run_dict(pr, count) for pr, count in rows.all()]


async def get_review_run(db, pr_id: int) -> dict | None:
    """one review run with all of its findings, or None if the id doesn't exist"""
    pr = (await db.execute(select(PullRequest).where(PullRequest.id == pr_id))).scalar_one_or_none()
    if pr is None:
        return None
    findings = (await db.execute(
        select(Review).where(Review.pr_id == pr_id).order_by(Review.id)
    )).scalars().all()
    return {**_review_run_dict(pr, len(findings)), "findings": [_finding_dict(f) for f in findings]}


async def list_findings(
    db,
    repo: str | None = None,
    severity: str | None = None,
    agent_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """findings across review runs, newest first, optionally filtered"""
    query = (
        select(Review, PullRequest.repo_name, PullRequest.github_pr_number)
        .join(PullRequest, Review.pr_id == PullRequest.id)
        .order_by(Review.id.desc())
        .limit(_clamp(limit))
    )
    if repo:
        query = query.where(PullRequest.repo_name == repo)
    if severity:
        query = query.where(Review.severity == severity)
    if agent_type:
        query = query.where(Review.agent_type == agent_type)
    rows = await db.execute(query)
    return [
        {**_finding_dict(r), "repo": repo_name, "pr_number": pr_number}
        for r, repo_name, pr_number in rows.all()
    ]


async def review_stats(db, repo: str | None = None) -> dict:
    """counts of review runs and findings, broken down by severity and agent"""
    runs = select(func.count(PullRequest.id))
    by_severity = select(Review.severity, func.count(Review.id)).join(PullRequest).group_by(Review.severity)
    by_agent = select(Review.agent_type, func.count(Review.id)).join(PullRequest).group_by(Review.agent_type)
    if repo:
        runs = runs.where(PullRequest.repo_name == repo)
        by_severity = by_severity.where(PullRequest.repo_name == repo)
        by_agent = by_agent.where(PullRequest.repo_name == repo)

    severity_counts = {s: c for s, c in (await db.execute(by_severity)).all()}
    agent_counts = {a: c for a, c in (await db.execute(by_agent)).all()}
    return {
        "repo": repo,
        "review_runs": (await db.execute(runs)).scalar() or 0,
        "total_findings": sum(severity_counts.values()),
        "findings_by_severity": severity_counts,
        "findings_by_agent": agent_counts,
    }


async def find_similar_past_issues(db, repo: str, code: str, k: int = 5) -> list[dict]:
    """
    review memory as a tool: past findings in `repo` whose code looks like `code`.
    same embeddings + FAISS search the review pipeline uses before each review.
    """
    rows = await db.execute(
        select(Review, PullRequest.github_pr_number)
        .join(PullRequest, Review.pr_id == PullRequest.id)
        .where(PullRequest.repo_name == repo, Review.embedding.is_not(None))
        .order_by(Review.id.desc())
        .limit(MEMORY_SEARCH_LIMIT)
    )
    past = [
        {
            "embedding": from_bytes(r.embedding),
            **_finding_dict(r),
            "pr_number": pr_number,
            "code_snippet": r.code_snippet,
        }
        for r, pr_number in rows.all()
    ]
    return find_similar([code], past, k=max(1, min(k, 20)))
