from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from backend.config import settings

# Async engine — non-blocking DB queries
# Why async? FastAPI is async — blocking DB calls would freeze your server
# pool_size=10 — 10 persistent connections shared across all requests
# max_overflow=20 — 20 extra connections allowed during traffic spikes
engine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,  # off by default — logging every query is noisy and can leak data into logs
    pool_size=10,
    max_overflow=20
)

# Session factory — creates a new session per request
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False  # objects stay usable after commit
)

class Base(DeclarativeBase):
    """All models inherit from this — SQLAlchemy tracks them for migrations"""
    pass

async def get_db():
    """
    FastAPI dependency injection — automatically provides DB session per request.
    Why yield? Guarantees session closes even if request throws an exception.
    """
    async with AsyncSessionLocal() as session:
        yield session