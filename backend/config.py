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
        env_file = ".env"

# Single instance imported everywhere — never instantiate Settings() twice
settings = Settings()