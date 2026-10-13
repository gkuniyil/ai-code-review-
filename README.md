# AI Code Review Platform

A full-stack platform that automatically reviews GitHub pull requests using AI to catch bugs, find security flaws, and improve code quality.

## Tech Stack
* Backend: FastAPI (Python)
* Database: PostgreSQL (with SQLAlchemy, async)
* Queue & Cache: Celery & Redis (for handling heavy background tasks)
* AI Engine: 4 parallel review agents orchestrated via LangGraph, on a switchable provider — Groq (`openai/gpt-oss-120b` by default, set with `GROQ_MODEL`) or Anthropic Claude (`claude-opus-5-5`)
* Review memory (RAG): Hugging Face `all-MiniLM-L6-v2` embeddings + FAISS similarity search

## Quick Setup

### 1. Set up your environment
Create a .env file in the root folder with your credentials:

DATABASE_URL=postgresql+asyncpg://user:password@db:5432/codereview
REDIS_URL=redis://redis:6379/0
GITHUB_WEBHOOK_SECRET=your_webhook_secret
GITHUB_TOKEN=your_github_token
GROQ_API_KEY=your_groq_api_key

To review with Claude instead of Groq:

LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_api_key
# optional: REVIEW_CLAUDE_MODEL (default claude-opus-5-5), REVIEW_CLAUDE_EFFORT (low|medium|high|xhigh|max, default high)

### 2. Run with Docker
Start the entire platform (backend, database, and background workers) with one command:

docker compose up -d --build

Once running, you can open your browser and go to http://localhost:8000/docs to see the interactive API dashboard.

## How it works
1. GitHub sends a webhook (`POST /api/v1/webhook`) on `opened`/`synchronize` PR events. The signature is HMAC-verified and the delivery is deduplicated before a `PullRequest` row is written to Postgres.
2. The request enqueues a Celery job on Redis and returns immediately — the FastAPI process never blocks on the AI review.
3. A Celery worker fetches the PR diff via the GitHub API, runs it through 4 parallel Groq-backed review agents (security, bug, quality, optimization) orchestrated by a LangGraph graph, filters findings through a basic safety check (`QualityRuleEvaluator`), saves them to Postgres, and posts them back to the PR as **one GitHub review**: a summary plus inline comments. Findings whose line number isn't part of the diff (which GitHub would reject) appear in the summary only.
   * Every push to a PR is reviewed: `opened` and `synchronize` deliveries each create their own review run, deduplicated by GitHub's delivery id.
   * Retries are safe: findings from a failed attempt are replaced rather than duplicated, and once the review is posted its id is stored (`github_review_id`), so a retry never posts it twice. Each review body also carries a hidden marker (`<!-- ai-code-reviewer run:<id> -->`); before posting, the worker checks GitHub for it, so even an attempt that posted but crashed before saving the id isn't re-posted.
   * **LLM provider is switchable** (`LLM_PROVIDER`): the rest of the app only calls `analyze_pr()` in `backend/ai_client.py`. On Claude, the response is constrained to the `PRReview` Pydantic schema with structured outputs (`messages.parse(output_format=PRReview)`), so it can't come back as invalid JSON the way free-form Groq output can. The system prompt and diff are identical for all four agents and come first in the request, marked for prompt caching, so retries (and later agents, once the cache is written) reuse them; agent-specific instructions follow. Policy refusals fall back to another model server-side (`fallbacks: "default"`), and a remaining refusal raises so Celery retries.
   * **Review memory (RAG):** each saved finding also stores the code it pointed at and an embedding of that code (`backend/embeddings.py`). Before the agents run, the new diff is split into small chunks and searched with FAISS against past findings **from the same repo**; matches above a similarity cutoff are added to the matching agent's prompt as hints (the security agent only sees past security findings, etc.).
     * Code is matched against *code*, not against finding text — in testing, code-vs-comment similarity was too noisy to rank correctly, while code-vs-code reliably surfaced related findings (~0.4–0.6) and ignored unrelated code (< 0.2).
     * Postgres is the source of truth for embeddings; the FAISS index is rebuilt per review from those rows, so memory survives restarts and is shared by every worker process. Exact search (`IndexFlatIP`) over the latest 2,000 findings per repo is fast at this scale — millions of vectors would call for an approximate index or pgvector.
     * Memory is best-effort: if the lookup or the embedding model fails, the review still runs without hints.
4. `GET /metrics` and `GET /reviews/{pr_id}/score` expose aggregate stats and a per-PR review quality score for the dashboard.
5. An **MCP server** (`backend/mcp_tools.py`) lets an AI assistant such as Claude Code or Claude Desktop query the review history.

## MCP server
Read-only tools over the review history:

| Tool | What it returns |
|---|---|
| `list_reviews(repo?, limit?)` | Recent review runs with status and finding counts |
| `get_review(pr_id)` | One review run and all its findings |
| `list_findings(repo?, severity?, agent_type?, limit?)` | Findings across runs, filtered |
| `get_review_stats(repo?)` | Counts by severity and agent |
| `find_similar_past_issues(repo, code, k?)` | Review-memory search: past findings whose code resembles `code` |

Design notes:
* **Read-only by design.** No tool can trigger a review, post to GitHub, or write to the database, so a confused or prompt-injected assistant can't cause damage through it. Every tool is annotated `read_only_hint=True`.
* **Separate process and dependency set.** The MCP SDK needs newer Starlette/Pydantic than the pinned FastAPI version, so it has its own `requirements-mcp.txt` and runs as its own process. It only needs to reach Postgres.
* Responses are capped (100 rows, 20 similarity hits) to stay within an assistant's context window.

Run it against the Docker Compose database. The connection string uses `localhost` because the server runs on your machine, not inside the Docker network:
```bash
python3.11 -m venv .venv-mcp && .venv-mcp/bin/pip install -r requirements-mcp.txt
```
Then register it with Claude Code by adding a `.mcp.json` in the repo root:
```json
{
  "mcpServers": {
    "ai-code-reviewer": {
      "command": ".venv-mcp/bin/python",
      "args": ["-m", "backend.mcp_tools"],
      "env": { "DATABASE_URL": "postgresql+asyncpg://codereviewer:password123@localhost:5432/codereviewer_db" }
    }
  }
}
```
The other settings (`GITHUB_TOKEN`, etc.) are still read from `.env` because `backend/config.py` requires them, even though the MCP server never uses them.

## Live test
Run end to end against a real private GitHub repo: a PR with five planted problems (hardcoded password, SQL injection, divide-by-zero on an empty list, an N+1 query, missing docstrings) was reviewed in ~12 s with Groq `openai/gpt-oss-120b`. All five were found by the expected agents, and all 10 inline comments landed on the correct lines. The run surfaced four bugs that unit tests with mocks had missed, all fixed with regression tests:
* Groq had retired `llama-3.3-70b-versatile` (404 `model_not_found`).
* `asyncio.run()` per task + a shared async connection pool broke every retry ("attached to a different loop"); the pool is now disposed when each task ends.
* GitHub review ids exceed 32 bits, so saving `github_review_id` overflowed an `INTEGER` column *after* the review was posted, and each retry posted another copy (4 identical reviews). Now `BIGINT`, and posting is idempotent via the hidden marker.
* `CLAUDE_EFFORT` collided with an environment variable set by Claude Code (caught while verifying the Claude request).

## Known limitations
* Agents can overlap: the same line may be flagged by two agents (e.g. string-built SQL as both a security and a bug issue). Findings aren't deduplicated across agents yet.
* The MCP server loads the full app `Settings`, so it needs the GitHub/Groq secrets in `.env` even though it never uses them. Giving it its own minimal settings would follow least privilege more closely.
* Past findings recalled from memory are LLM-generated text derived from earlier PRs, so they're framed in the prompt as hints, not instructions — but like the diff itself they're untrusted input and a possible prompt-injection path.
* No database migrations — tables are created with `create_all()`, which doesn't alter existing tables. A database created before the latest schema changes needs:
  ```sql
  ALTER TABLE pull_requests DROP CONSTRAINT IF EXISTS uq_repo_pr_number;
  ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS github_review_id BIGINT;
  ALTER TABLE pull_requests ALTER COLUMN github_review_id TYPE BIGINT;
  ALTER TABLE reviews ADD COLUMN IF NOT EXISTS code_snippet TEXT;
  ALTER TABLE reviews ADD COLUMN IF NOT EXISTS embedding BYTEA;
  ```
  (or `docker compose down -v` to start from an empty database). Alembic would be the production fix.
* If Redis is unreachable when a webhook arrives, the `PullRequest` row is saved but the job is never queued, so it stays `pending`. A transactional outbox (or a periodic sweep of stale `pending` rows) would fix this.
* `GET /metrics` and `GET /reviews/{pr_id}/score` are unauthenticated.
* The four agents call the LLM at the same moment, so on Claude they usually all miss the prompt cache on the first attempt (the cache entry is written only once a response starts). Caching mainly pays off on retries; running one agent first to warm the cache would trade latency for cost.
* Tests (74) cover the Groq and Claude providers (request shape, refusals, provider-specific config), webhook signature verification/dedup, the LangGraph agent fan-out/fan-in, GitHub API calls and diff-line parsing, the review task's retry/idempotency behavior, review memory (retrieval, thresholds, graceful failure — using a fake embedding model), the MCP server's tools and read-only annotations, its queries (against in-memory SQLite), and the safety filter. CI runs the MCP tests in a separate job with `requirements-mcp.txt` — they do not include an automated integration test against real Postgres/Redis/GitHub.
