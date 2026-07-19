import hmac                    # python built-in for HMAC signature computation
import hashlib                 # provides sha256 algorithm
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select  # used to query postgresql
from backend.database import get_db      # yields db session per request
from backend.models import PullRequest   # the table we're inserting into
from backend.config import settings      # access to env variables
from backend.worker import celery_app    # to queue background tasks
from backend.middleware import limiter

router = APIRouter()  # groups webhook routes, registered in main.py with /api/v1 prefix


def verify_signature(payload: bytes, signature: str) -> bool:
    """
    Proves the request is really from GitHub.
    GitHub computes HMAC(secret, payload) and sends result in header.
    We compute the same thing and compare — if they match its legit.
    """
    # feed secret + payload into HMAC SHA256 calculator
    # key must be bytes so we encode the secret string
    # msg is the raw request body github sent us
    mac = hmac.new(
        key=settings.github_webhook_secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256  # must match what github uses
    )

    # hexdigest() gives us the result as a readable hex string
    # github always prefixes with "sha256=" so we match that format
    expected = "sha256=" + mac.hexdigest()

    # compare_digest instead of == prevents timing attacks
    # == stops early when it finds a mismatch — attacker can measure timing
    # compare_digest always takes same time regardless of where mismatch is
    return hmac.compare_digest(expected, signature)

@router.post("/webhook")
@limiter.limit("100/minute")  # max 100 webhooks per minute per IP
@router.post("/webhook")
async def github_webhook(
    request: Request,                    # raw http request object
    db: AsyncSession = Depends(get_db)   # db session injected automatically
):
    # read raw bytes of request body — needed for hmac calculation
    # must read as bytes not json because hmac works on raw bytes
    payload = await request.body()

    # github sends signature in this header — "sha256=abc123..."
    signature = request.headers.get("X-Hub-Signature-256", "")

    # unique id github assigns to every webhook delivery
    # used to detect if github sends same webhook twice
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    # type of github event — pull_request, push, issue, etc
    event_type = request.headers.get("X-GitHub-Event", "")

    # if signature doesnt match → fake request → reject with 401
    # 401 = unauthorized, identity could not be verified
    if not verify_signature(payload, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # parse raw bytes into python dictionary so we can access fields
    # body["pull_request"]["title"] etc
    body = await request.json()

    # github sends webhooks for pushes, issues, comments too
    # we only care about pull_request events
    if event_type != "pull_request":
        return {"status": "ignored"}

    # github sends many pull_request actions — closed, labeled, merged etc
    # opened = new PR created
    # synchronize = new commits pushed to existing PR
    action = body.get("action")
    if action not in ["opened", "synchronize"]:
        return {"status": "ignored"}

    # github sometimes retries webhooks if your server was slow to respond
    # delivery_id is unique per delivery so we can detect duplicates
    # if we already processed this delivery_id → skip it
    result = await db.execute(
        select(PullRequest).where(
            PullRequest.github_delivery_id == delivery_id
        )
    )
    if result.scalar_one_or_none():
        return {"status": "duplicate"}

    # extract pr data from payload and store in postgresql
    # diff is empty for now — github_client.py fetches it in Day 3
    # status starts as "pending" — updates as pipeline progresses
    pr = PullRequest(
        github_delivery_id=delivery_id,
        github_pr_number=body["pull_request"]["number"],
        repo_name=body["repository"]["full_name"],
        title=body["pull_request"]["title"],
        author=body["pull_request"]["user"]["login"],
        diff="",
        status="pending"
    )
    db.add(pr)           # stage the insert — not written yet
    await db.commit()    # actually write to postgresql
    await db.refresh(pr) # fetch auto-generated id back from postgresql

    # drop job into redis queue instantly
    # celery worker picks it up and runs full ai review in background
    # we pass pr.id so celery knows which pr to process
    celery_app.send_task("backend.tasks.review_pr", args=[pr.id])

    # respond to github immediately — must be within 10 seconds
    # actual review happens async in background via celery
    return {"status": "queued", "pr_id": pr.id}