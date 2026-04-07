"""Inventory Rebalancer — detects balance deviation and suggests transfers.

Checks every 4 hours. When an exchange's share deviates > 30% from
target allocation, suggests a transfer to rebalance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.core.balance_tracker import BalanceTracker
from src.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class TransferSuggestion:
    """A suggested inter-exchange transfer."""

    from_exchange: str
    to_exchange: str
    amount_usd: float
    reason: str


class InventoryRebalancer:
    """매 4시간 잔고 체크, 편차 > 30% 시 이체 제안.

    - 거래소 간 잔고 편차 감지
    - 이체 제안 목록 생성
    - Telegram 경고 콜백
    """

    def __init__(
        self,
        tracker: BalanceTracker,
        deviation_threshold: float = 0.30,
        check_interval_s: float = 14400.0,  # 4 hours
        min_transfer_usd: float = 50.0,
    ) -> None:
        self.tracker = tracker
        self.deviation_threshold = deviation_threshold
        self.check_interval_s = check_interval_s
        self.min_transfer_usd = min_transfer_usd
        self._target_weights: dict[str, float] = {}

    def set_target_weights(self, weights: dict[str, float]) -> None:
        """Set target allocation weights per exchange (should sum to 1.0)."""
        total = sum(weights.values())
        if total > 0:
            self._target_weights = {k: v / total for k, v in weights.items()}
        else:
            self._target_weights = weights

    def compute_deviations(self) -> dict[str, float]:
        """Compute deviation from target for each exchange.

        Returns:
            {exchange_id: deviation} where deviation = actual_pct - target_pct.
            Positive = over-allocated, Negative = under-allocated.
        """
        total = self.tracker.get_total_balance()
        if total <= 0:
            return {}

        exchanges = self.tracker.get_all_exchanges()
        if not exchanges:
            return {}

        # If no target weights set, assume equal distribution
        if not self._target_weights:
            equal_weight = 1.0 / len(exchanges)
            targets = {eid: equal_weight for eid in exchanges}
        else:
            targets = self._target_weights

        deviations: dict[str, float] = {}
        for eid in exchanges:
            snap = self.tracker.get_latest(eid)
            if snap is None:
                continue
            actual_pct = snap.available_usd / total
            target_pct = targets.get(eid, 0.0)
            deviations[eid] = actual_pct - target_pct

        return deviations

    def check_and_suggest(self) -> list[TransferSuggestion]:
        """Check deviations and generate transfer suggestions.

        Only suggests transfers when deviation exceeds threshold.
        """
        deviations = self.compute_deviations()
        if not deviations:
            return []

        total = self.tracker.get_total_balance()
        over: list[tuple[str, float]] = []
        under: list[tuple[str, float]] = []

        for eid, dev in deviations.items():
            if dev > self.deviation_threshold:
                over.append((eid, dev))
            elif dev < -self.deviation_threshold:
                under.append((eid, dev))

        suggestions: list[TransferSuggestion] = []

        # Match over-allocated with under-allocated
        for over_eid, over_dev in sorted(over, key=lambda x: -x[1]):
            for under_eid, under_dev in sorted(under, key=lambda x: x[1]):
                transfer_pct = min(over_dev, abs(under_dev)) / 2.0
                amount = transfer_pct * total

                if amount < self.min_transfer_usd:
                    continue

                suggestions.append(
                    TransferSuggestion(
                        from_exchange=over_eid,
                        to_exchange=under_eid,
                        amount_usd=round(amount, 2),
                        reason=(
                            f"{over_eid} over by {over_dev:.1%}, "
                            f"{under_eid} under by {abs(under_dev):.1%}"
                        ),
                    )
                )

                logger.info(
                    "rebalancer.suggestion: %s → %s $%.2f (%s)",
                    over_eid, under_eid, amount, suggestions[-1].reason,
                )

        return suggestions

    def has_critical_imbalance(self) -> bool:
        """Check if any exchange has critical imbalance (> 2x threshold)."""
        deviations = self.compute_deviations()
        return any(
            abs(dev) > self.deviation_threshold * 2
            for dev in deviations.values()
        )

    async def connect_exchange_feeds(self, exchanges: dict) -> None:
        """Connect exchange balance feeds for live mode."""
        if get_settings().operational.execution_mode.lower() != "live":
            logger.info("Balance feed: simulation mode (no live exchange connection)")
            return

        for name, adapter in exchanges.items():
            try:
                balances = await adapter.get_balances()
                usdt = balances.get("USDT")
                if usdt:
                    self.tracker.record_balance(
                        exchange_id=name,
                        total_usd=float(usdt.total),
                        available_usd=float(usdt.free),
                        locked_usd=float(usdt.used),
                    )
                    logger.info("Balance feed connected: %s total=%.2f USDT", name, float(usdt.total))
            except Exception as exc:
                logger.warning("Balance feed failed for %s: %s", name, exc)
