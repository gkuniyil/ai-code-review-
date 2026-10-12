from pathlib import Path
from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Central config — reads all environment variables from .env
    Pydantic validates types automatically.
    Missing variable = immediate crash with clear error message.
    Why this over os.getenv()? Type safety + validation + single source of truth.
    """
    database_url: str
    redis_url: str
    github_webhook_secret: str
    github_token: str
    sql_echo: bool = False  # set SQL_ECHO=true to log every SQL query while debugging

    # LLM provider for the review agents: "groq" (Llama 3.3, free tier) or "anthropic" (Claude)
    llm_provider: Literal["groq", "anthropic"] = "groq"
    groq_api_key: str | None = None
    anthropic_api_key: str | None = None   # optional: the SDK also reads ANTHROPIC_API_KEY / `ant auth login`
    # REVIEW_ prefix on purpose: CLAUDE_EFFORT and ANTHROPIC_MODEL are already used by
    # other tools (e.g. Claude Code sets CLAUDE_EFFORT), and would silently override these
    review_claude_model: str = "claude-opus-5-5"
    review_claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    @model_validator(mode="after")
    def require_key_for_chosen_provider(self):
        # still fail fast at startup, but only for the provider actually in use
        if self.llm_provider == "groq" and not self.groq_api_key:
            raise ValueError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
        return self

    class Config:
        # absolute path so .env is found no matter which directory the process starts in
        # (e.g. the MCP server, which Claude launches from its own working directory)
        env_file = Path(__file__).resolve().parent.parent / ".env"

# Single instance imported everywhere — never instantiate Settings() twice
settings = Settings()