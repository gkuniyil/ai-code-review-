# AI Code Review Platform

A full-stack platform that automatically reviews GitHub pull requests using AI to catch bugs, find security flaws, and improve code quality.

## Tech Stack
* Backend: FastAPI (Python)
* Database: PostgreSQL (with SQLAlchemy, async)
* Queue & Cache: Celery & Redis (for handling heavy background tasks)
* AI Engine: Groq (`llama-3.3-70b-versatile`), orchestrated as 4 parallel review agents via LangGraph
* Review memory (RAG): Hugging Face `all-MiniLM-L6-v2` embeddings + FAISS similarity search

## Quick Setup

### 1. Set up your environment
Create a .env file in the root folder with your credentials:

DATABASE_URL=postgresql+asyncpg://user:password@db:5432/codereview
REDIS_URL=redis://redis:6379/0
GITHUB_WEBHOOK_SECRET=your_webhook_secret
GITHUB_TOKEN=your_github_token
GROQ_API_KEY=your_groq_api_key

### 2. Run with Docker
Start the entire platform (backend, database, and background workers) with one command:

docker compose up -d --build

Once running, you can open your browser and go to http://localhost:8000/docs to see the interactive API dashboard.

## How it works
1. GitHub sends a webhook (`POST /api/v1/webhook`) on `opened`/`synchronize` PR events. The signature is HMAC-verified and the delivery is deduplicated before a `PullRequest` row is written to Postgres.
2. The request enqueues a Celery job on Redis and returns immediately — the FastAPI process never blocks on the AI review.
3. A Celery worker fetches the PR diff via the GitHub API, runs it through 4 parallel Groq-backed review agents (security, bug, quality, optimization) orchestrated by a LangGraph graph, filters findings through a basic safety check (`QualityRuleEvaluator`), saves them to Postgres, and posts them back to the PR as **one GitHub review**: a summary plus inline comments. Findings whose line number isn't part of the diff (which GitHub would reject) appear in the summary only.
   * Every push to a PR is reviewed: `opened` and `synchronize` deliveries each create their own review run, deduplicated by GitHub's delivery id.
   * Retries are safe: findings from a failed attempt are replaced rather than duplicated, and once the review is posted its id is stored (`github_review_id`), so a retry never posts it twice.
   * **Review memory (RAG):** each saved finding also stores the code it pointed at and an embedding of that code (`backend/embeddings.py`). Before the agents run, the new diff is split into small chunks and searched with FAISS against past findings **from the same repo**; matches above a similarity cutoff are added to the matching agent's prompt as hints (the security agent only sees past security findings, etc.).
     * Code is matched against *code*, not against finding text — in testing, code-vs-comment similarity was too noisy to rank correctly, while code-vs-code reliably surfaced related findings (~0.4–0.6) and ignored unrelated code (< 0.2).
     * Postgres is the source of truth for embeddings; the FAISS index is rebuilt per review from those rows, so memory survives restarts and is shared by every worker process. Exact search (`IndexFlatIP`) over the latest 2,000 findings per repo is fast at this scale — millions of vectors would call for an approximate index or pgvector.
     * Memory is best-effort: if the lookup or the embedding model fails, the review still runs without hints.
4. `GET /metrics` and `GET /reviews/{pr_id}/score` expose aggregate stats and a per-PR review quality score for the dashboard.

## Known limitations
* `backend/mcp_tools.py` is an empty placeholder — no MCP integration exists yet.
* Past findings recalled from memory are LLM-generated text derived from earlier PRs, so they're framed in the prompt as hints, not instructions — but like the diff itself they're untrusted input and a possible prompt-injection path.
* No database migrations — tables are created with `create_all()`, which doesn't alter existing tables. A database created before the latest schema changes needs:
  ```sql
  ALTER TABLE pull_requests DROP CONSTRAINT IF EXISTS uq_repo_pr_number;
  ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS github_review_id INTEGER;
  ALTER TABLE reviews ADD COLUMN IF NOT EXISTS code_snippet TEXT;
  ALTER TABLE reviews ADD COLUMN IF NOT EXISTS embedding BYTEA;
  ```
  (or `docker compose down -v` to start from an empty database). Alembic would be the production fix.
* If Redis is unreachable when a webhook arrives, the `PullRequest` row is saved but the job is never queued, so it stays `pending`. A transactional outbox (or a periodic sweep of stale `pending` rows) would fix this.
* Each Celery task runs its async code with `asyncio.run()`, which creates a new event loop per task while the SQLAlchemy engine's connection pool is shared at module level. Pooled asyncpg connections are tied to the loop that created them, so this can fail on a worker's later tasks; disposing the engine per task (or using `NullPool` in the worker) would fix it.
* `GET /metrics` and `GET /reviews/{pr_id}/score` are unauthenticated.
* Tests (46) cover webhook signature verification/dedup, the LangGraph agent fan-out/fan-in, GitHub API calls and diff-line parsing, the review task's retry/idempotency behavior, review memory (retrieval, thresholds, graceful failure — using a fake embedding model), and the safety filter — they do not include a live integration test against real Postgres/Redis/GitHub.