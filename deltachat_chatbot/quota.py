"""Cooldown loop logic"""
import asyncio
import time
from datetime import datetime, timedelta

from deltabot_cli import BotCli
from sqlalchemy.future import select
from sqlalchemy.sql.expression import delete

from .orm import Usage, async_session, fetchone


class QuotaManager:
    """Manage user and global quotas"""

    def __init__(self, cli: BotCli, cfg: dict) -> None:
        self._cli = cli
        self._cfg = cfg
        self._rate_limit = 0
        self._lock = asyncio.Lock()

    async def cooldown_loop(self) -> None:
        next_month = await self.get_global_cooldown()
        while True:
            if self._rate_limit <= time.time():
                self._rate_limit = 0
            if next_month <= time.time():
                async with self._lock:
                    self._cli.set_custom_config("used_tokens", "0")
                next_month = _get_next_month_timestamp()
                await self._cli.set_custom_config("next_month", str(next_month))

            async with async_session() as session:
                async with session.begin():
                    stmt = delete(Usage).filter(Usage.ends_at <= time.time())
                    await session.execute(stmt)
            await asyncio.sleep(5)

    async def increase_usage(self, user_id: int, tokens: int) -> None:
        # increase global usage
        async with self._lock:
            used_tokens = int(await self._cli.get_custom_config("used_tokens") or 0)
            await self._cli.set_custom_config("used_tokens", str(used_tokens + tokens))

        # increase user usage
        async with async_session() as session:
            async with session.begin():
                stmt = select(Usage).filter_by(user_id=user_id)
                usage = await fetchone(session, stmt)
                if not usage:
                    usage = Usage(user_id=user_id, ends_at=_get_next_hour_timestamp())
                    session.add(usage)
                usage.queries += 1
                usage.tokens += tokens

    def is_rate_limited(self) -> bool:
        return bool(self._rate_limit)

    def set_rate_limit(self, seconds: int) -> None:
        self._rate_limit = int(time.time()) + seconds

    async def get_global_cooldown(self) -> int:
        return int(await self._cli.get_custom_config("next_month") or 0)

    async def global_quota_exceeded(self) -> bool:
        global_quota = int(self._cfg.get("global_monthly_quota") or 0)
        used_tokens = int(await self._cli.get_custom_config("used_tokens") or 0)
        if global_quota and used_tokens >= global_quota:
            return True
        return False

    async def quota_exceeded(self, user_id: int) -> int:
        """If quota was exceeded return the cooldown, otherwise return zero"""
        tokens_quota = int(self._cfg.get("user_hourly_tokens_quota") or 0)
        queries_quota = int(self._cfg.get("user_hourly_queries_quota") or 0)
        async with async_session() as session:
            async with session.begin():
                stmt = select(Usage).filter_by(user_id=user_id)
                usage = await fetchone(session, stmt)
                if usage:
                    if usage.tokens >= tokens_quota or usage.queries >= queries_quota:
                        return usage.ends_at
        return 0


def _get_next_month_timestamp() -> int:
    return int(
        (datetime.today().replace(day=25) + timedelta(days=7))
        .replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )


def _get_next_hour_timestamp() -> int:
    return int((datetime.today() + timedelta(hours=1)).timestamp())
