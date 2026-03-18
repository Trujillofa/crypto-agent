import asyncpg

from src.utils.logger import get_logger

logger = get_logger(__name__)


class LifecycleManager:
    def __init__(self, db_config):
        # Convert 'name' to 'database' for asyncpg
        self.db_config = dict(db_config)
        if "name" in self.db_config and "database" not in self.db_config:
            self.db_config["database"] = self.db_config.pop("name")
        self.conn = None

    async def __aenter__(self):
        self.conn = await asyncpg.connect(**self.db_config)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            await self.conn.close()

    async def get_strategy_status(self, name: str) -> str:
        """Get current status of strategy."""
        row = await self.conn.fetchrow(
            "SELECT status FROM strategy_versions WHERE name = $1 AND version = (SELECT MAX(version) FROM strategy_versions WHERE name = $1)",
            name,
        )
        return row["status"] if row else "unknown"

    async def _check_statuses(
        self,
        strategies: list[str],
        *,
        allowed_statuses: set[str],
        allow_unknown: bool = False,
        unknown_message: str | None = None,
    ) -> bool:
        """Check strategy lifecycle status against an allowed set."""
        all_allowed = True
        normalized_allowed = {status.lower() for status in allowed_statuses}

        for name in strategies:
            status = (await self.get_strategy_status(name)).lower()
            if status == "unknown" and allow_unknown:
                if unknown_message:
                    logger.info(unknown_message, name)
                continue
            if status not in normalized_allowed:
                logger.warning("Strategy %s status: %s (blocked)", name, status)
                all_allowed = False

        return all_allowed

    async def is_live(self, strategies: list[str]) -> bool:
        """Check if all strategies are 'live'."""
        return await self._check_statuses(strategies, allowed_statuses={"live"})

    async def is_paper_ready(self, strategies: list[str]) -> bool:
        """Check if strategies are acceptable for paper validation.

        Paper mode allows strategies that are already live, explicitly marked
        for paper validation, or only validated offline. Missing registry rows
        are logged as informational so new paper strategies do not look blocked
        before they earn lifecycle promotion.
        """
        return await self._check_statuses(
            strategies,
            allowed_statuses={"live", "validated", "paper"},
            allow_unknown=True,
            unknown_message="Strategy %s has no lifecycle row yet (allowed in paper mode)",
        )

    async def promote_strategy(self, name: str, version: str, metrics: dict):
        """Promote strategy to next state."""
        await self.conn.execute(
            """
            INSERT INTO strategy_versions (name, version, status, config, backtest_sharpe, backtest_win_rate)
            VALUES ($1, $2, 'validated', $3, $4, $5)
            ON CONFLICT (name, version) DO NOTHING
            """,
            name,
            version,
            metrics.get("config", "{}"),
            metrics.get("sharpe"),
            metrics.get("win_rate"),
        )
        logger.info(f"Promoted {name}:{version} with Sharpe {metrics.get('sharpe')}")
