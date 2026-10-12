from pathlib import Path
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
    groq_api_key: str
    sql_echo: bool = False  # set SQL_ECHO=true to log every SQL query while debugging

    class Config:
        # absolute path so .env is found no matter which directory the process starts in
        # (e.g. the MCP server, which Claude launches from its own working directory)
        env_file = Path(__file__).resolve().parent.parent / ".env"

# Single instance imported everywhere — never instantiate Settings() twice
settings = Settings()