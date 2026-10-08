import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.database import get_db
from backend.models import PullRequest
from backend.webhook import router, verify_signature

PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {"number": 42, "title": "Add feature", "user": {"login": "octocat"}},
    "repository": {"full_name": "octocat/hello-world"},
}


def sign(payload: bytes) -> str:
    mac = hmac.new(settings.github_webhook_secret.encode("utf-8"), payload, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def headers_for(body: bytes, event: str = "pull_request", delivery_id: str = "d1") -> dict:
    return {
        "X-Hub-Signature-256": sign(body),
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": event,
    }


class TestVerifySignature:
    def test_valid_signature_passes(self):
        payload = b'{"hello": "world"}'
        assert verify_signature(payload, sign(payload)) is True

    def test_invalid_signature_fails(self):
        payload = b'{"hello": "world"}'
        assert verify_signature(payload, "sha256=deadbeef") is False

    def test_tampered_payload_fails_even_with_valid_looking_signature(self):
        payload = b'{"hello": "world"}'
        signature = sign(payload)
        assert verify_signature(b'{"hello": "mallory"}', signature) is False


def _client_with_mock_db(existing_pr=None):
    """
    Builds a minimal FastAPI app around just the webhook router, with the
    get_db dependency swapped for a mock session — avoids needing a real
    Postgres instance to test request handling logic.
    """
    db_session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = existing_pr
    db_session.execute = AsyncMock(return_value=execute_result)
    db_session.commit = AsyncMock()
    db_session.rollback = AsyncMock()
    db_session.refresh = AsyncMock()

    async def override_get_db():
        yield db_session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), db_session


@patch("backend.webhook.celery_app")
def test_invalid_signature_returns_401(mock_celery):
    client, _ = _client_with_mock_db()
    body = json.dumps(PR_PAYLOAD).encode()
    resp = client.post(
        "/api/v1/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Delivery": "d1",
            "X-GitHub-Event": "pull_request",
        },
    )
    assert resp.status_code == 401
    mock_celery.send_task.assert_not_called()


@patch("backend.webhook.celery_app")
def test_non_pull_request_event_is_ignored(mock_celery):
    client, _ = _client_with_mock_db()
    body = json.dumps(PR_PAYLOAD).encode()
    resp = client.post("/api/v1/webhook", content=body, headers=headers_for(body, event="push"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}
    mock_celery.send_task.assert_not_called()


@patch("backend.webhook.celery_app")
def test_closed_action_is_ignored(mock_celery):
    client, _ = _client_with_mock_db()
    payload = {**PR_PAYLOAD, "action": "closed"}
    body = json.dumps(payload).encode()
    resp = client.post("/api/v1/webhook", content=body, headers=headers_for(body))
    assert resp.json() == {"status": "ignored"}
    mock_celery.send_task.assert_not_called()


@patch("backend.webhook.celery_app")
def test_duplicate_delivery_id_is_skipped(mock_celery):
    client, db_session = _client_with_mock_db(existing_pr=MagicMock())
    body = json.dumps(PR_PAYLOAD).encode()
    resp = client.post("/api/v1/webhook", content=body, headers=headers_for(body))
    assert resp.json() == {"status": "duplicate"}
    mock_celery.send_task.assert_not_called()
    db_session.commit.assert_not_called()


@patch("backend.webhook.celery_app")
def test_new_pr_is_saved_and_queued(mock_celery):
    client, db_session = _client_with_mock_db(existing_pr=None)

    async def fake_refresh(pr):
        pr.id = 99

    db_session.refresh.side_effect = fake_refresh

    body = json.dumps(PR_PAYLOAD).encode()
    resp = client.post("/api/v1/webhook", content=body, headers=headers_for(body))

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "pr_id": 99}
    db_session.add.assert_called_once()
    mock_celery.send_task.assert_called_once_with("backend.tasks.review_pr", args=[99])


@patch("backend.webhook.celery_app")
def test_new_commits_on_existing_pr_are_queued(mock_celery):
    """
    A "synchronize" delivery (new commits pushed) has a new delivery id but the
    same repo + PR number as an earlier review — it must be reviewed, not skipped.
    """
    client, db_session = _client_with_mock_db(existing_pr=None)

    async def fake_refresh(pr):
        pr.id = 100

    db_session.refresh.side_effect = fake_refresh

    payload = {**PR_PAYLOAD, "action": "synchronize"}
    body = json.dumps(payload).encode()
    resp = client.post("/api/v1/webhook", content=body, headers=headers_for(body, delivery_id="d2"))

    assert resp.json() == {"status": "queued", "pr_id": 100}
    mock_celery.send_task.assert_called_once_with("backend.tasks.review_pr", args=[100])


def test_schema_allows_multiple_review_runs_per_pr():
    """
    Regression test: a unique constraint on (repo_name, github_pr_number) made the
    second webhook for a PR fail with IntegrityError, which the endpoint reported
    as "duplicate" — so new commits were silently never reviewed.
    Only github_delivery_id should be unique.
    """
    table = PullRequest.__table__
    unique_column_sets = [
        set(c.columns.keys()) for c in table.constraints if isinstance(c, UniqueConstraint)
    ]
    unique_column_sets += [{c.name} for c in table.columns if c.unique]

    assert {"github_delivery_id"} in unique_column_sets
    assert not any("github_pr_number" in cols for cols in unique_column_sets)


@patch("backend.webhook.celery_app")
def test_concurrent_duplicate_race_is_handled_gracefully(mock_celery):
    """
    Regression test: the delivery_id pre-check is look-then-insert, not atomic.
    If two deliveries of the same webhook race past the check, the DB's unique
    constraint on github_delivery_id should still catch it via IntegrityError,
    and the endpoint should degrade to {"status": "duplicate"} instead of a 500.
    """
    client, db_session = _client_with_mock_db(existing_pr=None)
    db_session.commit.side_effect = IntegrityError("INSERT", {}, Exception("unique violation"))

    body = json.dumps(PR_PAYLOAD).encode()
    resp = client.post("/api/v1/webhook", content=body, headers=headers_for(body))

    assert resp.status_code == 200
    assert resp.json() == {"status": "duplicate"}
    db_session.rollback.assert_called_once()
    mock_celery.send_task.assert_not_called()
