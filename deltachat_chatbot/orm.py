"""database"""
import logging
from typing import Any

from sqlalchemy import Column, Integer
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql.selectable import Select

Base = declarative_base()
_session = None  # noqa


class Usage(Base):  # noqa
    """User quota stats"""

    __tablename__ = "usage"
    user_id = Column(Integer, primary_key=True)
    tokens = Column(Integer, nullable=False)
    queries = Column(Integer, nullable=False)
    ends_at = Column(Integer, nullable=False)

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("tokens", 0)
        kwargs.setdefault("queries", 0)
        super().__init__(**kwargs)


def async_session():
    """Get session"""
    return _session()


async def init(path: str, debug: bool = False) -> None:
    """Initialize engine."""
    global _session  # noqa
    engine = create_async_engine(path, echo=debug)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _session = sessionmaker(engine, class_=AsyncSession)


async def fetchone(session: sessionmaker, stmt: Select) -> Any:
    return (await session.execute(stmt.limit(1))).scalars().first()
