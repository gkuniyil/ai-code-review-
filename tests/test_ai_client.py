import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from backend import ai_client
from backend.ai_client import PRReview, ReviewFinding, analyze_pr
from backend.config import Settings

DIFF = "\n--- app.py ---\n@@ -0,0 +1,1 @@\n+password = 'hunter2'\n"


def _claude_response(review=None, stop_reason="end_turn", category=None):
    return SimpleNamespace(
        parsed_output=review,
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
    )


def _finding(agent_type="security"):
    return ReviewFinding(agent_type=agent_type, severity="high", file_path="app.py", line_number=1,
                         comment="hardcoded password", suggested_fix="read it from an env var")


@pytest.fixture
def use_claude():
    with patch.object(ai_client.settings, "llm_provider", "anthropic"), \
         patch.object(ai_client.settings, "review_claude_model", "claude-opus-5-5"), \
         patch.object(ai_client.settings, "review_claude_effort", "high"), \
         patch.object(ai_client, "_claude_client") as client_factory:
        yield client_factory.return_value


def test_claude_path_requests_structured_output_with_cached_diff(use_claude):
    use_claude.beta.messages.parse.return_value = _claude_response(PRReview(findings=[_finding()], summary="1 issue"))

    review = analyze_pr(DIFF, "security", "- [high] db.py: SQL injection")

    kwargs = use_claude.beta.messages.parse.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is PRReview          # response constrained to the Pydantic schema
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["fallbacks"] == "default"
    assert kwargs["system"] == ai_client.CLAUDE_SYSTEM_PROMPT
    diff_block, agent_block = kwargs["messages"][0]["content"]
    assert DIFF in diff_block["text"] and diff_block["cache_control"] == {"type": "ephemeral"}
    assert "security code reviewer" in agent_block["text"]
    assert "SQL injection" in agent_block["text"]        # memory hints come after the cached prefix
    assert review.findings[0].comment == "hardcoded password"


def test_claude_prefix_is_identical_across_agents(use_claude):
    """the cache only helps if the four agents send the exact same system + diff prefix"""
    use_claude.beta.messages.parse.return_value = _claude_response(PRReview(findings=[], summary="ok"))

    for agent in ("security", "bug", "quality", "optimization"):
        analyze_pr(DIFF, agent)

    prefixes = {
        (c.kwargs["system"], c.kwargs["messages"][0]["content"][0]["text"])
        for c in use_claude.beta.messages.parse.call_args_list
    }
    assert len(prefixes) == 1


def test_claude_findings_are_labelled_with_the_calling_agent(use_claude):
    use_claude.beta.messages.parse.return_value = _claude_response(
        PRReview(findings=[_finding(agent_type="Security ")], summary="")
    )

    assert analyze_pr(DIFF, "security").findings[0].agent_type == "security"


def test_claude_refusal_raises_so_celery_retries(use_claude):
    use_claude.beta.messages.parse.return_value = _claude_response(stop_reason="refusal", category="cyber")

    with pytest.raises(Exception, match="declined the security review.*cyber"):
        analyze_pr(DIFF, "security")


def test_claude_truncated_output_raises(use_claude):
    use_claude.beta.messages.parse.return_value = _claude_response(stop_reason="max_tokens")

    with pytest.raises(Exception, match="no complete bug review"):
        analyze_pr(DIFF, "bug")


def test_groq_path_parses_and_validates_json():
    payload = {"findings": [_finding().model_dump()], "summary": "1 issue"}
    groq = MagicMock()
    groq.chat.completions.create.return_value.choices = [
        SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))
    ]
    with patch.object(ai_client.settings, "llm_provider", "groq"), \
         patch.object(ai_client, "_groq_client", return_value=groq):
        review = analyze_pr(DIFF, "security", "- hint")

    assert review.findings[0].file_path == "app.py"
    assert "- hint" in groq.chat.completions.create.call_args.kwargs["messages"][1]["content"]


def test_groq_path_rejects_invalid_json():
    groq = MagicMock()
    groq.chat.completions.create.return_value.choices = [SimpleNamespace(message=SimpleNamespace(content="not json"))]
    with patch.object(ai_client.settings, "llm_provider", "groq"), \
         patch.object(ai_client, "_groq_client", return_value=groq):
        with pytest.raises(Exception, match="invalid JSON"):
            analyze_pr(DIFF, "bug")


REQUIRED = dict(database_url="postgresql+asyncpg://x", redis_url="redis://x", github_webhook_secret="s", github_token="t")


def test_settings_require_groq_key_only_when_groq_is_the_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    with pytest.raises(ValidationError, match="GROQ_API_KEY is required"):
        Settings(_env_file=None, **REQUIRED)

    claude = Settings(_env_file=None, llm_provider="anthropic", **REQUIRED)
    assert claude.review_claude_model == "claude-opus-5-5"


def test_settings_ignore_other_tools_env_vars(monkeypatch):
    """
    Regression test: the effort setting used to read CLAUDE_EFFORT, which Claude Code
    itself sets — running the app inside it silently changed the review effort.
    """
    monkeypatch.setenv("CLAUDE_EFFORT", "low")
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-other-model")

    s = Settings(_env_file=None, llm_provider="anthropic", **REQUIRED)

    assert s.review_claude_effort == "high"
    assert s.review_claude_model == "claude-opus-5-5"


def test_settings_reject_unknown_provider(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="openai", groq_api_key="k", **REQUIRED)
