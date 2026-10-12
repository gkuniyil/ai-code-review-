from backend.worker import celery_app
from backend.database import AsyncSessionLocal
from backend.models import PullRequest, Review
from backend.github_client import fetch_pr_diff, post_review, get_latest_commit_sha, commentable_lines, code_snippet, diff_chunks
from backend.embeddings import embed, find_similar, to_bytes, from_bytes
from sqlalchemy import select, delete
from backend.agents import review_graph
from backend.analytics import QualityRuleEvaluator
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)

# cap on how many past findings are loaded per review — bounds memory and search time
MEMORY_SEARCH_LIMIT = 2000


async def recall_similar_findings(db, pr, diff: str) -> list[dict]:
    """
    review memory lookup: finds past findings in the same repo whose code looks like
    the code in this diff. memory is an optional extra — if anything here fails the
    review still runs, just without hints.
    """
    try:
        # only this repo's history — other repos' findings would be irrelevant (and could leak their code)
        rows = await db.execute(
            select(Review)
            .join(PullRequest, Review.pr_id == PullRequest.id)
            .where(
                PullRequest.repo_name == pr.repo_name,
                Review.pr_id != pr.id,
                Review.embedding.is_not(None),
            )
            .order_by(Review.id.desc())
            .limit(MEMORY_SEARCH_LIMIT)
        )
        past = [
            {
                "embedding": from_bytes(r.embedding),
                "agent_type": r.agent_type,
                "severity": r.severity,
                "file_path": r.file_path,
                "comment": r.comment,
            }
            for r in rows.scalars().all()
        ]
        return find_similar(diff_chunks(diff), past)
    except Exception:
        logger.exception("review memory lookup failed for PR %s — continuing without it", pr.id)
        return []


def embed_findings(findings, diff: str) -> list[tuple[str | None, bytes | None]]:
    """
    for each finding: the code it points at, and that code's embedding, so future
    reviews can recall it. (None, None) when the line isn't in the diff; embeddings
    are skipped (None) if the model fails, without failing the review.
    """
    snippets = [code_snippet(diff, f.file_path, f.line_number) for f in findings]
    to_embed = [s for s in snippets if s]
    try:
        vectors = iter(embed(to_embed)) if to_embed else iter([])
        return [(s, to_bytes(next(vectors)) if s else None) for s in snippets]
    except Exception:
        logger.exception("embedding findings failed — saving them without memory")
        return [(s, None) for s in snippets]


@celery_app.task(name="backend.tasks.review_pr", bind=True, max_retries=3)
def review_pr(self, pr_id: int):
    """main celery task — orchestrates full PR review pipeline"""
    asyncio.run(_review_pr_async(self, pr_id))


async def _review_pr_async(task, pr_id: int):
    """async version — celery doesn't support async natively so we wrap in asyncio.run()"""
    async with AsyncSessionLocal() as db:
        pr = None  # define pr here so except block can access it even if fetch fails
        try:
            # fetch PR from PostgreSQL
            result = await db.execute(select(PullRequest).where(PullRequest.id == pr_id))
            pr = result.scalar_one_or_none()
            if not pr:
                raise Exception(f"PR {pr_id} not found in database")

            # review was already posted by an earlier attempt (e.g. the worker crashed
            # right after posting) — don't post it again, just finish marking it done
            if pr.github_review_id:
                pr.status = "completed"
                pr.completed_at = pr.completed_at or datetime.utcnow()
                await db.commit()
                return

            # update status so dashboard shows current stage
            pr.status = "fetching_diff"
            await db.commit()

            # fetch code changes from GitHub
            diff = fetch_pr_diff(pr.repo_name, pr.github_pr_number)

            # store diff so agents don't re-fetch from GitHub
            pr.diff = diff
            await db.commit()

            # update status + track when review started for metrics
            pr.status = "reviewing"
            pr.review_started_at = datetime.utcnow()
            await db.commit()

            # get latest commit SHA — needed to attach line comments to correct file version
            commit_sha = get_latest_commit_sha(pr.repo_name, pr.github_pr_number)

            # look up similar past findings in this repo — passed to the agents as hints
            similar_findings = await recall_similar_findings(db, pr, diff)

            # run all 4 agents in parallel with LangGraph — each focuses on different aspect of code
            result = review_graph.invoke({
                "diff": diff,
                "similar_findings": similar_findings,
                "security_findings": [],
                "bug_findings": [],
                "quality_findings": [],
                "optimization_findings": [],
                "all_findings": []
            })
            all_findings = result["all_findings"]

            # drop findings that fail basic safety checks (blank/hallucinated comments,
            # malformed markdown, unprofessional phrasing) before they reach GitHub or the DB
            all_findings = [
                f for f in all_findings
                if QualityRuleEvaluator.verify_review_safety(f.comment, f.suggested_fix)
            ]

            # clear findings saved by a previous failed attempt so a retry replaces them instead of duplicating
            await db.execute(delete(Review).where(Review.pr_id == pr.id))

            # save all findings to PostgreSQL, with the code + embedding that future reviews will search
            for finding, (snippet, embedding) in zip(all_findings, embed_findings(all_findings, diff)):
                db.add(Review(
                    pr_id=pr.id,
                    agent_type=finding.agent_type,
                    severity=finding.severity,
                    file_path=finding.file_path,
                    line_number=finding.line_number,
                    comment=finding.comment,
                    suggested_fix=finding.suggested_fix,
                    code_snippet=snippet,
                    embedding=embedding
                ))
            await db.commit()

            # inline comments only for lines github will accept — the LLM can point at a file
            # or line outside the diff, which would make github reject the whole review (422).
            # findings without a valid line still appear in the summary below.
            valid_lines = commentable_lines(diff)
            inline_comments = [
                {
                    "path": finding.file_path,
                    "line": finding.line_number,
                    "body": f"**{finding.agent_type.upper()} - {finding.severity.upper()}**\n{finding.comment}\n\n**Suggested Fix:** {finding.suggested_fix}",
                }
                for finding in all_findings
                if finding.line_number in valid_lines.get(finding.file_path, set())
            ]

            # build overall summary shown at the top of the review
            summary = f"🤖 AI Code Review Complete\n\nFound {len(all_findings)} issues:\n"
            for finding in all_findings:
                summary += f"- **{finding.severity.upper()}** ({finding.agent_type}): {finding.comment}\n"

            # post summary + inline comments as one review, then record it immediately
            # so a retry after this point can't post a second copy
            pr.github_review_id = post_review(
                pr.repo_name, pr.github_pr_number, commit_sha, summary, inline_comments
            )
            await db.commit()

            # mark completed + record timestamp for metrics
            pr.status = "completed"
            pr.completed_at = datetime.utcnow()
            await db.commit()

        except Exception as exc:
            if pr:  # only update if pr was successfully fetched
                await db.rollback()  # discard half-finished work from this attempt (e.g. unsaved findings)
                # "failed" only once retries are used up — before that the review is still in progress
                pr.status = "retrying" if task.request.retries < task.max_retries else "failed"
                await db.commit()
            raise task.retry(exc=exc, countdown=60)