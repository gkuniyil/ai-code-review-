from backend.worker import celery_app
from backend.database import AsyncSessionLocal
from backend.models import PullRequest, Review
from backend.github_client import fetch_pr_diff, post_review, get_latest_commit_sha, commentable_lines
from sqlalchemy import select, delete
from backend.agents import review_graph
from backend.analytics import QualityRuleEvaluator
from datetime import datetime
import asyncio

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

            # run all 4 agents — each focuses on different aspect of code
            # NEW — LangGraph parallel execution

            result = review_graph.invoke({
                "diff": diff,
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

            # save all findings to PostgreSQL
            for finding in all_findings:
                db.add(Review(
                    pr_id=pr.id,
                    agent_type=finding.agent_type,
                    severity=finding.severity,
                    file_path=finding.file_path,
                    line_number=finding.line_number,
                    comment=finding.comment,
                    suggested_fix=finding.suggested_fix
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