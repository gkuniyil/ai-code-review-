from fastapi import FastAPI
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from backend.database import engine, Base
from backend.webhook import router as webhook_router
from backend.middleware import limiter
from backend.config import settings
from sqlalchemy import func, text, select
from backend.database import AsyncSessionLocal
from backend.models import PullRequest, Review
from backend.evals import score_review_quality
import redis.asyncio as redis_async

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title="AI Code Reviewer",
    description="Event-driven multi-agent code review platform",
    version="1.0.0",
    lifespan=lifespan
)

# add rate limiter to app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(webhook_router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    """
    checks that the app can actually reach its dependencies —
    a static 200 tells you the process is alive, not that it's usable.
    """
    db_ok = True
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    redis_ok = True
    try:
        redis_client = redis_async.from_url(settings.redis_url)
        await redis_client.ping()
        await redis_client.close()
    except Exception:
        redis_ok = False

    healthy = db_ok and redis_ok
    body = {
        "status": "ok" if healthy else "degraded",
        "service": "ai-code-reviewer",
        "database": "ok" if db_ok else "unreachable",
        "redis": "ok" if redis_ok else "unreachable",
    }
    return JSONResponse(content=body, status_code=200 if healthy else 503)

@app.get("/metrics")
async def get_metrics():
    """
    returns real performance metrics from PostgreSQL.
    used by dashboard + README to show system is working.
    """
    async with AsyncSessionLocal() as db:
        # total PRs reviewed
        total_prs = await db.execute(
            select(func.count(PullRequest.id))
            .where(PullRequest.status == "completed")
        )
        total_prs = total_prs.scalar()

        # average review time in seconds
        avg_time = await db.execute(
            select(func.avg(
                func.extract("epoch", PullRequest.completed_at) -
                func.extract("epoch", PullRequest.created_at)
            ))
            .where(PullRequest.status == "completed")
        )
        avg_time = round(avg_time.scalar() or 0, 2)

        # findings by severity
        severity_counts = await db.execute(
            select(Review.severity, func.count(Review.id))
            .group_by(Review.severity)
        )
        severity = {row[0]: row[1] for row in severity_counts}

        # findings by agent type
        agent_counts = await db.execute(
            select(Review.agent_type, func.count(Review.id))
            .group_by(Review.agent_type)
        )
        agents = {row[0]: row[1] for row in agent_counts}

        # total findings
        total_findings = await db.execute(select(func.count(Review.id)))
        total_findings = total_findings.scalar()

    return {
        "total_prs_reviewed": total_prs,
        "total_findings": total_findings,
        "avg_review_time_seconds": avg_time,
        "findings_by_severity": severity,
        "findings_by_agent": agents
    }

@app.get("/reviews/{pr_id}/score")
async def get_review_score(pr_id: int):
    """
    scores how complete/actionable a PR's review findings are.
    see backend/evals.py for the scoring rubric.
    """
    return await score_review_quality(pr_id)