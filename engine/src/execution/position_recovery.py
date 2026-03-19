"""Position Recovery — recovers open positions after abnormal shutdown.

On startup, queries execution_log for trades without matching close,
then decides: close stale positions or resume tracking.
Uses Redis WAL pattern for in-flight trade state persistence.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class RecoveryAction(str, Enum):
    CLOSE = "CLOSE"      # Force-close stale position
    RESUME = "RESUME"    # Resume tracking (recent)
    SKIP = "SKIP"        # Already closed / no action needed


@dataclass
class OpenPosition:
    """An open position found during recovery scan."""

    trade_id: str
    strategy_id: str
    exchange_id: str
    symbol: str
    side: str  # "buy" or "sell"
    size: float
    entry_price: float
    opened_at: datetime
    age_seconds: float = 0.0


@dataclass
class RecoveryResult:
    """Result of position recovery scan."""

    positions_found: int = 0
    closed: int = 0
    resumed: int = 0
    skipped: int = 0
    actions: list[dict] = field(default_factory=list)


class PositionRecovery:
    """비정상 종료 후 오픈 포지션 조회 + 정리.

    - Redis WAL: 진행 중 거래 상태를 Redis에 기록
    - Recovery: 시작 시 Redis에서 미완료 거래 조회
    - Decision: age > stale_threshold → CLOSE, else → RESUME
    """

    def __init__(
        self,
        stale_threshold_s: float = 300.0,  # 5 minutes
        redis_prefix: str = "leviathan:wal:",
        # US-250: Accept redis client directly for async scan/reconcile
        redis=None,
        stale_threshold_seconds: float | None = None,
    ) -> None:
        # stale_threshold_seconds overrides stale_threshold_s if provided
        self.stale_threshold_s = stale_threshold_seconds if stale_threshold_seconds is not None else stale_threshold_s
        self.redis_prefix = redis_prefix
        self._redis = redis  # async or sync redis client

    def write_wal(self, redis_conn, trade_id: str, state: dict) -> None:
        """Write trade state to Redis WAL before execution."""
        key = f"{self.redis_prefix}{trade_id}"
        redis_conn.set(key, json.dumps(state), ex=3600)  # 1hr TTL
        logger.debug("wal.write: %s", trade_id)

    def clear_wal(self, redis_conn, trade_id: str) -> None:
        """Clear WAL entry after successful completion."""
        key = f"{self.redis_prefix}{trade_id}"
        redis_conn.delete(key)
        logger.debug("wal.clear: %s", trade_id)

    def scan_wal(self, redis_conn) -> list[OpenPosition]:
        """Scan Redis for incomplete WAL entries."""
        pattern = f"{self.redis_prefix}*"
        positions: list[OpenPosition] = []

        keys = redis_conn.keys(pattern)
        for key in keys:
            raw = redis_conn.get(key)
            if raw is None:
                continue

            try:
                state = json.loads(raw)
                opened_str = state.get("opened_at", "")
                opened_at = (
                    datetime.fromisoformat(opened_str)
                    if opened_str
                    else datetime.now(timezone.utc)
                )
                age = (datetime.now(timezone.utc) - opened_at).total_seconds()

                positions.append(
                    OpenPosition(
                        trade_id=state.get("trade_id", key),
                        strategy_id=state.get("strategy_id", "unknown"),
                        exchange_id=state.get("exchange_id", "unknown"),
                        symbol=state.get("symbol", ""),
                        side=state.get("side", "buy"),
                        size=float(state.get("size", 0)),
                        entry_price=float(state.get("entry_price", 0)),
                        opened_at=opened_at,
                        age_seconds=age,
                    )
                )
            except (json.JSONDecodeError, ValueError, KeyError) as exc:
                logger.warning("wal.parse_error: key=%s err=%s", key, exc)

        return positions

    def decide_action(self, position: OpenPosition) -> RecoveryAction:
        """Decide recovery action based on position age."""
        if position.size <= 0:
            return RecoveryAction.SKIP
        if position.age_seconds > self.stale_threshold_s:
            return RecoveryAction.CLOSE
        return RecoveryAction.RESUME

    def recover(self, redis_conn) -> RecoveryResult:
        """Full recovery: scan WAL → decide → execute actions."""
        positions = self.scan_wal(redis_conn)
        result = RecoveryResult(positions_found=len(positions))

        for pos in positions:
            action = self.decide_action(pos)

            if action == RecoveryAction.CLOSE:
                self.clear_wal(redis_conn, pos.trade_id)
                result.closed += 1
                logger.info(
                    "recovery.close: %s %s %s age=%.0fs",
                    pos.trade_id, pos.symbol, pos.exchange_id, pos.age_seconds,
                )
            elif action == RecoveryAction.RESUME:
                result.resumed += 1
                logger.info(
                    "recovery.resume: %s %s age=%.0fs",
                    pos.trade_id, pos.symbol, pos.age_seconds,
                )
            else:
                self.clear_wal(redis_conn, pos.trade_id)
                result.skipped += 1

            result.actions.append({
                "trade_id": pos.trade_id,
                "action": action.value,
                "symbol": pos.symbol,
                "exchange": pos.exchange_id,
                "age_s": round(pos.age_seconds, 1),
            })

        logger.info(
            "recovery.complete: found=%d closed=%d resumed=%d skipped=%d",
            result.positions_found, result.closed, result.resumed, result.skipped,
        )

        return result

    async def scan(self) -> "RecoveryResult":
        """US-250: Async scan using stored redis client.

        Scans WAL for incomplete entries and decides recovery action.
        Uses self._redis (passed in __init__) instead of a passed-in conn.
        """
        if self._redis is None:
            return RecoveryResult()
        try:
            # Support both sync and async redis clients
            keys_coro = self._redis.keys(f"{self.redis_prefix}*")
            if hasattr(keys_coro, "__await__"):
                keys = await keys_coro
            else:
                keys = keys_coro
        except Exception as exc:
            logger.warning("scan.keys_error: %s", exc)
            return RecoveryResult()

        result = RecoveryResult(positions_found=0)
        for key in keys:
            try:
                raw_coro = self._redis.get(key)
                raw = await raw_coro if hasattr(raw_coro, "__await__") else raw_coro
                if raw is None:
                    continue
                state = json.loads(raw)
                opened_str = state.get("opened_at", "")
                opened_at = (
                    datetime.fromisoformat(opened_str)
                    if opened_str
                    else datetime.now(timezone.utc)
                )
                age = (datetime.now(timezone.utc) - opened_at).total_seconds()
                pos = OpenPosition(
                    trade_id=state.get("trade_id", str(key)),
                    strategy_id=state.get("strategy_id", "unknown"),
                    exchange_id=state.get("exchange_id", "unknown"),
                    symbol=state.get("symbol", ""),
                    side=state.get("side", "buy"),
                    size=float(state.get("size", 0)),
                    entry_price=float(state.get("entry_price", 0)),
                    opened_at=opened_at,
                    age_seconds=age,
                )
                result.positions_found += 1
                action = self.decide_action(pos)
                if action == RecoveryAction.CLOSE:
                    result.closed += 1
                    logger.info("recovery.close: %s age=%.0fs", pos.trade_id, age)
                elif action == RecoveryAction.RESUME:
                    result.resumed += 1
                    logger.info("recovery.resume: %s age=%.0fs", pos.trade_id, age)
                else:
                    result.skipped += 1
                result.actions.append({
                    "trade_id": pos.trade_id,
                    "action": action.value,
                    "symbol": pos.symbol,
                    "age_s": round(age, 1),
                })
            except Exception as exc:
                logger.warning("scan.parse_error: key=%s err=%s", key, exc)

        logger.info(
            "recovery.scan_complete: found=%d closed=%d resumed=%d skipped=%d",
            result.positions_found, result.closed, result.resumed, result.skipped,
        )
        return result

    async def reconcile(self) -> "RecoveryResult":
        """US-250: Alias for scan() — periodic reconciliation entry point."""
        return await self.scan()
