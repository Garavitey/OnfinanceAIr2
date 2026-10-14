"""Async database engine/session setup.

Reads DATABASE_URL from the environment (via python-dotenv) so the same code
runs against both the local SQLite default and a Postgres deployment —
never hardcode a connection string here.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./uml.db")

engine = create_async_engine(DATABASE_URL)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a transactional AsyncSession."""
    async with AsyncSessionLocal() as session:
        yield session
