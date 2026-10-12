"""
mcp_tools.py

MCP (Model Context Protocol) server — lets an AI assistant like Claude Code or
Claude Desktop query this platform's review history as tools: "what high-severity
security issues were found in this repo?", "does this code look like anything
we've flagged before?"

runs as its own process, separate from the FastAPI app and Celery worker:
    python -m backend.mcp_tools          (stdio — the assistant launches it)

every tool is read-only: the server can't trigger reviews, post to GitHub, or
change the database, so a confused or prompt-injected assistant can't do damage
through it. needs only DATABASE_URL to be reachable.

dependencies are in requirements-mcp.txt — the MCP SDK needs newer
starlette/pydantic than the FastAPI app pins, so it gets its own environment.
"""
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from backend import review_queries
from backend.database import AsyncSessionLocal

server = MCPServer(
    name="ai-code-reviewer",
    instructions=(
        "Read-only access to the AI code reviewer's history: review runs (one per "
        "pull-request push), the findings its security/bug/quality/optimization agents "
        "reported, aggregate stats, and similarity search over past findings' code."
    ),
)

# tells the client these tools only read data — safe to call without confirmation
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


@server.tool(annotations=READ_ONLY)
async def list_reviews(repo: str | None = None, limit: int = 10) -> list[dict]:
    """
    List the most recent review runs, newest first. Each run is one webhook delivery
    (a PR being opened or receiving new commits) and includes its status and number
    of findings. repo is "owner/name"; limit is capped at 100.
    """
    async with AsyncSessionLocal() as db:
        return await review_queries.list_review_runs(db, repo=repo, limit=limit)


@server.tool(annotations=READ_ONLY)
async def get_review(pr_id: int) -> dict:
    """
    Get one review run and all of its findings. pr_id is the internal review-run id
    returned by list_reviews (not the GitHub PR number).
    """
    async with AsyncSessionLocal() as db:
        review = await review_queries.get_review_run(db, pr_id)
    if review is None:
        # ToolError (not ValueError) so the message reaches the assistant — other
        # exceptions are reported only as a generic "Error executing tool"
        raise ToolError(f"no review run with id {pr_id} — use list_reviews to find ids")
    return review


@server.tool(annotations=READ_ONLY)
async def list_findings(
    repo: str | None = None,
    severity: str | None = None,
    agent_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    List findings across all review runs, newest first. Optional filters:
    repo ("owner/name"), severity ("high", "medium", "low"), and agent_type
    ("security", "bug", "quality", "optimization"). limit is capped at 100.
    """
    async with AsyncSessionLocal() as db:
        return await review_queries.list_findings(
            db, repo=repo, severity=severity, agent_type=agent_type, limit=limit
        )


@server.tool(annotations=READ_ONLY)
async def get_review_stats(repo: str | None = None) -> dict:
    """
    Count review runs and findings, broken down by severity and by agent.
    Pass repo ("owner/name") for one repository, or omit it for all of them.
    """
    async with AsyncSessionLocal() as db:
        return await review_queries.review_stats(db, repo=repo)


@server.tool(annotations=READ_ONLY)
async def find_similar_past_issues(repo: str, code: str, k: int = 5) -> list[dict]:
    """
    Search review memory: given a code snippet, return past findings in repo
    ("owner/name") whose flagged code is most similar, with a similarity score.
    Useful for "has code like this caused problems here before?". k is capped at 20.
    The first call loads the embedding model, which takes a few seconds.
    """
    async with AsyncSessionLocal() as db:
        return await review_queries.find_similar_past_issues(db, repo=repo, code=code, k=k)


if __name__ == "__main__":
    server.run()  # stdio transport — the MCP client starts this process and talks over stdin/stdout
