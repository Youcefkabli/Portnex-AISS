"""
Database module stubbed for simplified (no-DB) deployment.

Live path uses in-memory state only. This module exists so app.db imports
do not break; AsyncSessionLocal and get_db must not be used.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

# Placeholder; not used when DB is disabled
AsyncSessionLocal = None  # type: ignore[assignment]


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Not available when running without database."""
    raise RuntimeError("Database is disabled in this deployment")
