from backend.models import Review
from backend.database import AsyncSessionLocal
from sqlalchemy import select


async def score_review_quality(pr_id: int) -> dict:
    """
    scores the quality of a PR review.
    checks if findings have all required fields populated.
    returns a score between 0 and 1.
    
    interviewers ask: how do you know if your AI is doing a good job?
    this is your answer.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Review).where(Review.pr_id == pr_id)
        )
        findings = result.scalars().all()

    if not findings:
        return {"score": 0, "reason": "no findings generated"}

    scores = []
    for finding in findings:
        score = 0

        # has a comment (required)
        if finding.comment and len(finding.comment) > 20:
            score += 0.4

        # has a suggested fix (more useful)
        if finding.suggested_fix:
            score += 0.3

        # has a specific line number (more actionable)
        if finding.line_number:
            score += 0.2

        # severity is valid
        if finding.severity in ["high", "medium", "low"]:
            score += 0.1

        scores.append(score)

    avg_score = sum(scores) / len(scores)

    return {
        "pr_id": pr_id,
        "total_findings": len(findings),
        "avg_quality_score": round(avg_score, 2),
        "high_severity_count": sum(1 for f in findings if f.severity == "high"),
        "findings_with_fixes": sum(1 for f in findings if f.suggested_fix),
        "findings_with_line_numbers": sum(1 for f in findings if f.line_number)
    }