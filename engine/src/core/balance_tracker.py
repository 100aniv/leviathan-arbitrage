"""Balance Tracker — polls exchange balances and maintains history.

Polls each exchange every 5 minutes via REST API, stores snapshots,
and triggers Telegram alerts when balance drops below threshold.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class BalanceSnapshot:
    """Single point-in-time balance for one exchange."""

    exchange_id: str
    timestamp: datetime
    total_usd: float
    available_usd: float
    locked_usd: float = 0.0
    assets: dict[str, float] = field(default_factory=dict)


class BalanceTracker:
    """거래소별 잔고 폴링 + 이력 저장.

    - 매 5분 거래소별 잔고 조회
    - 이력 저장 (in-memory ring buffer)
    - 임계치 이하 시 alert 콜백 호출
    - 잔고 부족 시 거래 규모 축소 비율 계산
    """

    def __init__(
        self,
        min_balance_usd: float = 100.0,
        history_max: int = 288,  # 24h at 5min intervals
        poll_interval_s: float = 300.0,
    ) -> None:
        self.min_balance_usd = min_balance_usd
        self.history_max = history_max
        self.poll_interval_s = poll_interval_s
        self._history: dict[str, list[BalanceSnapshot]] = {}
        self._latest: dict[str, BalanceSnapshot] = {}
        self._alert_callback = None

    def set_alert_callback(self, callback) -> None:
        """Set async callback for low-balance alerts: callback(exchange_id, balance)."""
        self._alert_callback = callback

    def record_balance(
        self,
        exchange_id: str,
        total_usd: float,
        available_usd: float,
        locked_usd: float = 0.0,
        assets: dict[str, float] | None = None,
    ) -> BalanceSnapshot:
        """Record a balance snapshot for an exchange."""
        snapshot = BalanceSnapshot(
            exchange_id=exchange_id,
            timestamp=datetime.now(timezone.utc),
            total_usd=total_usd,
            available_usd=available_usd,
            locked_usd=locked_usd,
            assets=assets or {},
        )

        if exchange_id not in self._history:
            self._history[exchange_id] = []

        hist = self._history[exchange_id]
        hist.append(snapshot)
        if len(hist) > self.history_max:
            hist.pop(0)

        self._latest[exchange_id] = snapshot

        logger.debug(
            "balance_tracker.recorded: %s total=$%.2f avail=$%.2f",
            exchange_id, total_usd, available_usd,
        )

        return snapshot

    def is_below_threshold(self, exchange_id: str) -> bool:
        """Check if exchange balance is below minimum threshold."""
        snap = self._latest.get(exchange_id)
        if snap is None:
            return False
        return snap.available_usd < self.min_balance_usd

    def get_low_balance_exchanges(self) -> list[str]:
        """Return list of exchanges with balance below threshold."""
        return [
            eid for eid in self._latest
            if self.is_below_threshold(eid)
        ]

    def compute_size_scale(self, exchange_id: str, target_size_usd: float) -> float:
        """Compute trade size scale factor based on available balance.

        Returns:
            Scale factor in [0.0, 1.0]. 1.0 = full size, 0.0 = cannot trade.
        """
        snap = self._latest.get(exchange_id)
        if snap is None or target_size_usd <= 0:
            return 0.0

        if snap.available_usd >= target_size_usd:
            return 1.0

        if snap.available_usd <= 0:
            return 0.0

        return snap.available_usd / target_size_usd

    def get_total_balance(self) -> float:
        """Sum of all exchange available balances."""
        return sum(s.available_usd for s in self._latest.values())

    def get_latest(self, exchange_id: str) -> BalanceSnapshot | None:
        """Get most recent snapshot for exchange."""
        return self._latest.get(exchange_id)

    def get_history(self, exchange_id: str) -> list[BalanceSnapshot]:
        """Get balance history for exchange."""
        return self._history.get(exchange_id, [])

    def get_all_exchanges(self) -> list[str]:
        """Get list of tracked exchange IDs."""
        return list(self._latest.keys())
