from fastapi import FastAPI
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from backend.database import engine, Base
from backend.webhook import router as webhook_router
from backend.middleware import limiter
from sqlalchemy import func, text, select 
from backend.database import AsyncSessionLocal
from backend.models import PullRequest, Review

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
    return {"status": "ok", "service": "ai-code-reviewer"}

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