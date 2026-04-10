"""LEVIATHAN Risk Guardian — Pre-Trade Checks.

Implements 11 pre-trade checks (Amendment 1E, 3C, 7, US-154).
Check #0 (halt check) CANNOT be bypassed — uses threading.Event, no external deps.

Check ordering:
  #0:  Halt check (threading.Event, < 0.01ms) — CANNOT be bypassed
  #1:  Position limit
  #2:  Drawdown limit
  #3:  Exposure limit
  #4:  Circuit breaker state
  #4e: Net exposure per asset (Amendment 7)
  #5:  Exchange health score
  #6:  Max single trade size
  #7:  Volatility check
  #8:  Max rollback cost gate (Amendment 3C)
  #9:  Strategy correlation scale-down (log-only, US-118)
  #10: Max concurrent positions (US-154)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog

from src.infra.metrics import RISK_REJECTIONS_TOTAL
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.kill_switch import halt_local, is_halted

if TYPE_CHECKING:
    from src.risk.correlation_monitor import CorrelationMonitor

logger = structlog.get_logger(__name__)


@dataclass
class TradeProposal:
    """Represents a proposed trade for risk checking."""

    strategy_id: str
    exchange_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    size: Decimal
    price: Decimal
    position_value: Decimal
    predicted_slippage_pct: Decimal = Decimal("0.001")
    fee_open: Decimal = Decimal("0.001")
    fee_close: Decimal = Decimal("0.001")


@dataclass
class RiskCheckResult:
    approved: bool
    rejected_at_check: int | None = None
    reason: str = ""


@dataclass
class PortfolioState:
    """Snapshot of current portfolio state for risk checks."""

    total_capital: Decimal
    used_capital: Decimal
    current_drawdown_pct: Decimal
    total_exposure: Decimal
    position_sizes: dict[str, Decimal]       # symbol -> position_value
    exchange_health_scores: dict[str, Decimal]  # exchange_id -> score (0-1)
    volatility_1min: dict[str, Decimal]      # symbol -> 1-min vol
    volatility_24h: dict[str, Decimal]       # symbol -> 24h avg vol
    # (exchange_id, base_asset) -> net quantity (long=positive, short=negative)
    # Populated from ExposureTracker before calling check(). Used for Amendment 7.
    net_exposures: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    # US-196: strategy_base -> total capital currently used by that strategy
    strategy_capital_used: dict[str, Decimal] = field(default_factory=dict)


class RiskGuardian:
    """
    Pre-trade risk guardian implementing 9 checks.

    Check #0 is a halt check via threading.Event — CANNOT be bypassed.
    Every order submission path MUST call check() before proceeding.
    """

    def __init__(
        self,
        circuit_breaker: CircuitBreaker,
        max_position_pct: Decimal = Decimal("0.10"),
        max_drawdown_pct: Decimal = Decimal("0.02"),
        max_exposure_pct: Decimal = Decimal("0.30"),
        exchange_health_threshold: Decimal = Decimal("0.50"),  # PHOENIX Phase 2: 0.90→0.50 (live calibration)
        max_single_trade_pct: Decimal = Decimal("0.05"),
        max_volatility_multiple: Decimal = Decimal("2.0"),
        max_rollback_threshold: Decimal = Decimal("0.02"),
        max_net_exposure_per_asset: Decimal = Decimal("0"),
        max_concurrent_positions: int = 20,
        dynamic_sizer: Any | None = None,  # US-176: DynamicSizer for correlation scale-down
        capital_allocation_pct: dict[str, float] | None = None,  # US-196: per-strategy capital limits
        warmup_seconds: float = 120.0,  # cold-start grace period for exchange health check
    ) -> None:
        from src.core.config_loader import get_config as _gc
        self._cb = circuit_breaker
        self._start_time: float = time.monotonic()
        self._warmup_seconds: float = float(warmup_seconds)
        self._dynamic_sizer = dynamic_sizer  # US-176
        self._max_position_pct = max_position_pct
        self._max_drawdown_pct = max_drawdown_pct
        self._max_exposure_pct = max_exposure_pct
        self._exchange_health_threshold = exchange_health_threshold
        self._max_single_trade_pct = max_single_trade_pct
        self._max_volatility_multiple = max_volatility_multiple
        self._max_rollback_threshold = max_rollback_threshold
        # Amendment 7: 0 = disabled (no correlation check)
        self._max_net_exposure_per_asset = max_net_exposure_per_asset
        # US-154: max concurrent open positions (HIGH FIX: bounds validation)
        try:
            _mcp = int(_gc("execution.max_concurrent_trades", default=max_concurrent_positions))
        except (ValueError, TypeError):
            _mcp = max_concurrent_positions
        self._max_concurrent_positions: int = max(1, min(_mcp, 1000))
        # US-196: per-strategy capital allocation limits
        self._capital_allocation_pct: dict[str, float] = capital_allocation_pct or {}
        # US-222/228: per-strategy circuit breaker (optional, set externally)
        self.per_strategy_cb: Any | None = None
        if self._capital_allocation_pct:
            total_alloc = sum(self._capital_allocation_pct.values())
            if total_alloc > 100.0:
                raise ValueError(
                    f"Capital allocation sum exceeds 100%: {total_alloc:.1f}%"
                )
            if total_alloc < 80.0:
                logger.warning(
                    "capital_allocation_low",
                    total_pct=total_alloc,
                    msg=f"Capital allocation sum is {total_alloc:.1f}% (< 80%), "
                        f"{100.0 - total_alloc:.1f}% will remain idle",
                )
        # US-118: optional correlation monitor — set externally after construction
        self.correlation_monitor: CorrelationMonitor | None = None
        # US-278: optional portfolio risk manager — set externally after construction
        self.portfolio_risk: Any | None = None
        # US-286: optional DataQualityManager — set externally after construction
        self.data_quality_manager: Any | None = None
        # SIT-3: FlashGuard — rapid price movement detection, set externally after construction
        self.flash_guard: Any | None = None
        # US-280: emergency halt flag (set by emergency_pause, read by check #0 via is_halted)
        self._halted: bool = False

    def check(self, proposal: TradeProposal, portfolio: PortfolioState) -> RiskCheckResult:
        """
        Run all 9 pre-trade risk checks sequentially.

        Returns RiskCheckResult with approved=True only if ALL checks pass.
        Check #0 (halt) CANNOT be bypassed and is always first.
        """
        # CHECK #0: Halt check (Amendment 1E) — CANNOT be bypassed
        # Uses threading.Event — works without Redis, PostgreSQL, or any external dep.
        if is_halted():
            logger.warning(
                "risk_check_0_halt",
                strategy=proposal.strategy_id,
                exchange=proposal.exchange_id,
            )
            RISK_REJECTIONS_TOTAL.labels(check_number="0", reason="engine_halted").inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=0,
                reason="Engine is halted (kill switch active)",
            )

        # CHECK #0.5: FlashGuard — rapid price movement detection (SIT-3)
        if self.flash_guard is not None:
            _fg_allowed, _fg_reason = self.flash_guard.check_allowed()
            if not _fg_allowed:
                RISK_REJECTIONS_TOTAL.labels(check_number="0.5", reason="flash_guard").inc()
                return RiskCheckResult(
                    approved=False,
                    rejected_at_check=0,
                    reason=_fg_reason,
                )

        # CHECK #1: Position limit
        existing_position = portfolio.position_sizes.get(proposal.symbol, Decimal("0"))
        new_position_total = existing_position + proposal.position_value
        max_position_value = portfolio.total_capital * self._max_position_pct

        if new_position_total > max_position_value:
            RISK_REJECTIONS_TOTAL.labels(check_number="1", reason="position_limit").inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=1,
                reason=(
                    f"Position limit exceeded: {new_position_total:.2f} > "
                    f"{max_position_value:.2f} "
                    f"(max {self._max_position_pct * 100:.1f}% of capital)"
                ),
            )

        # CHECK #2: Drawdown limit
        if portfolio.current_drawdown_pct > self._max_drawdown_pct:
            RISK_REJECTIONS_TOTAL.labels(check_number="2", reason="drawdown_limit").inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=2,
                reason=(
                    f"Drawdown limit exceeded: "
                    f"{portfolio.current_drawdown_pct * 100:.2f}% > "
                    f"{self._max_drawdown_pct * 100:.2f}%"
                ),
            )

        # CHECK #3: Exposure limit
        new_total_exposure = portfolio.total_exposure + proposal.position_value
        max_exposure = portfolio.total_capital * self._max_exposure_pct

        if new_total_exposure > max_exposure:
            RISK_REJECTIONS_TOTAL.labels(check_number="3", reason="exposure_limit").inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=3,
                reason=(
                    f"Exposure limit exceeded: {new_total_exposure:.2f} > "
                    f"{max_exposure:.2f} "
                    f"(max {self._max_exposure_pct * 100:.1f}% of capital)"
                ),
            )

        # CHECK #4: Circuit breaker state
        if not self._cb.allows_trading():
            RISK_REJECTIONS_TOTAL.labels(check_number="4", reason="circuit_breaker_open").inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=4,
                reason=f"Circuit breaker is {self._cb.state.value} — trading halted",
            )

        # CHECK #4 (enhanced) — Amendment 7 Scenario 5: Cross-strategy correlation.
        # Compute hypothetical net_exposure = current + proposed_delta.
        # REJECT if |net_exposure| > max_net_exposure_per_asset (when configured).
        if self._max_net_exposure_per_asset > Decimal("0") and "/" in proposal.symbol:
            base_asset = proposal.symbol.split("/")[0]
            net_key = (proposal.exchange_id, base_asset)
            current_net = portfolio.net_exposures.get(net_key, Decimal("0"))
            delta = proposal.size if proposal.side.upper() == "BUY" else -proposal.size
            hypothetical_net = current_net + delta
            if abs(hypothetical_net) > self._max_net_exposure_per_asset:
                RISK_REJECTIONS_TOTAL.labels(
                    check_number="4", reason="net_exposure_exceeded"
                ).inc()
                return RiskCheckResult(
                    approved=False,
                    rejected_at_check=4,
                    reason=(
                        f"Net exposure limit exceeded (Amendment 7): "
                        f"|{hypothetical_net}| > {self._max_net_exposure_per_asset} "
                        f"for {proposal.exchange_id}:{base_asset}"
                    ),
                )

        # CHECK #5: Exchange health score (US-286: prefer DQM if available)
        # Cold-start warm-up: skip health check during initial warmup_seconds to allow
        # WebSocket connections and latency metrics to stabilise before enforcing threshold.
        _in_warmup = (time.monotonic() - self._start_time) < self._warmup_seconds
        if self.data_quality_manager is not None:
            _dqm_score = self.data_quality_manager.get_health_score(proposal.exchange_id)
            health_score = Decimal(str(_dqm_score))
        else:
            health_score = portfolio.exchange_health_scores.get(
                proposal.exchange_id, Decimal("0")
            )
        if not _in_warmup and health_score < self._exchange_health_threshold:
            RISK_REJECTIONS_TOTAL.labels(
                check_number="5", reason="exchange_health_low"
            ).inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=5,
                reason=(
                    f"Exchange {proposal.exchange_id} health score too low: "
                    f"{health_score:.3f} < {self._exchange_health_threshold:.3f}"
                ),
            )

        # CHECK #6: Max single trade size
        max_trade_value = portfolio.total_capital * self._max_single_trade_pct
        if proposal.position_value > max_trade_value:
            RISK_REJECTIONS_TOTAL.labels(
                check_number="6", reason="trade_size_exceeded"
            ).inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=6,
                reason=(
                    f"Trade size too large: {proposal.position_value:.2f} > "
                    f"{max_trade_value:.2f} "
                    f"(max {self._max_single_trade_pct * 100:.1f}% of capital)"
                ),
            )

        # CHECK #7: Volatility check (skip if no data)
        vol_1min = portfolio.volatility_1min.get(proposal.symbol)
        vol_24h = portfolio.volatility_24h.get(proposal.symbol)

        if vol_1min is not None and vol_24h is not None and vol_24h > Decimal("0"):
            vol_ratio = vol_1min / vol_24h
            if vol_ratio > self._max_volatility_multiple:
                RISK_REJECTIONS_TOTAL.labels(
                    check_number="7", reason="volatility_too_high"
                ).inc()
                return RiskCheckResult(
                    approved=False,
                    rejected_at_check=7,
                    reason=(
                        f"Volatility too high: 1min/24h ratio {vol_ratio:.2f} > "
                        f"{self._max_volatility_multiple:.1f}x"
                    ),
                )

        # CHECK #8: Max rollback cost gate (Amendment 3C)
        # worst_case_slippage_pct = 3 * predicted_slippage_pct
        # (3x multiplier: rollbacks occur during adverse conditions)
        worst_case_slippage = Decimal("3") * proposal.predicted_slippage_pct
        round_trip_fees = proposal.fee_open + proposal.fee_close
        max_rollback_cost = proposal.position_value * (worst_case_slippage + round_trip_fees)
        rollback_threshold_value = proposal.position_value * self._max_rollback_threshold

        if max_rollback_cost > rollback_threshold_value:
            RISK_REJECTIONS_TOTAL.labels(
                check_number="8", reason="rollback_cost_exceeded"
            ).inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=8,
                reason=(
                    f"Max rollback cost too high: {max_rollback_cost:.4f} > "
                    f"{rollback_threshold_value:.4f} "
                    f"({self._max_rollback_threshold * 100:.1f}% of position)"
                ),
            )

        # CHECK #9: Strategy correlation scale-down (US-118 / US-176 / US-264)
        if self.correlation_monitor is not None:
            events = self.correlation_monitor.check_correlations()
            for evt in events:
                if evt.strategy_id == proposal.strategy_id and evt.scale < 1.0:
                    logger.warning(
                        "risk_check_9_correlation_scale",
                        strategy=proposal.strategy_id,
                        scale=evt.scale,
                        reason=evt.reason,
                    )
                    # US-264: ENFORCE position scaling (was log-only before US-264)
                    if self._dynamic_sizer is not None:
                        self._dynamic_sizer.set_correlation_scale(proposal.strategy_id, evt.scale)
                    # US-264: Apply scale directly to proposal size even without DynamicSizer
                    proposal.size = proposal.size * Decimal(str(evt.scale))
                    RISK_REJECTIONS_TOTAL.labels(
                        check_number="9", reason="correlation_scale_down"
                    ).inc()
                    logger.info(
                        "risk_check_9_enforced scale=%.2f new_size=%.6f strategy=%s reason=%s",
                        evt.scale, float(proposal.size), proposal.strategy_id, evt.reason,
                    )
                    break

        # CHECK #10: Max concurrent positions (US-154)
        if len(portfolio.position_sizes) >= self._max_concurrent_positions:
            RISK_REJECTIONS_TOTAL.labels(
                check_number="10", reason="max_concurrent_positions"
            ).inc()
            return RiskCheckResult(
                approved=False,
                rejected_at_check=10,
                reason=(
                    f"Max concurrent positions reached: "
                    f"{len(portfolio.position_sizes)} >= {self._max_concurrent_positions}"
                ),
            )

        # CHECK #11: Per-strategy capital allocation (US-196)
        if self._capital_allocation_pct:
            # Extract base strategy name (e.g., "cross_exchange_v1" -> "cross_exchange")
            strategy_base = proposal.strategy_id.split("_v")[0] if "_v" in proposal.strategy_id else proposal.strategy_id
            alloc_pct = self._capital_allocation_pct.get(strategy_base)
            if alloc_pct is not None:
                max_strategy_capital = portfolio.total_capital * Decimal(str(alloc_pct)) / Decimal("100")
                # US-196 fix: use strategy_capital_used (keyed by strategy_base)
                strategy_used = portfolio.strategy_capital_used.get(strategy_base, Decimal("0"))
                if strategy_used + proposal.position_value > max_strategy_capital:
                    RISK_REJECTIONS_TOTAL.labels(
                        check_number="11", reason="capital_allocation_exceeded"
                    ).inc()
                    return RiskCheckResult(
                        approved=False,
                        rejected_at_check=11,
                        reason=(
                            f"Strategy capital allocation exceeded: "
                            f"{strategy_base} used {strategy_used:.2f} + {proposal.position_value:.2f} > "
                            f"{max_strategy_capital:.2f} ({alloc_pct}% of capital)"
                        ),
                    )

        # CHECK #12: Per-strategy circuit breaker (US-222/228)
        if self.per_strategy_cb is not None:
            # Global CB override: if halted >= active → open global circuit breaker
            halted = self.per_strategy_cb.halted_count()
            active = self.per_strategy_cb.active_count()
            if active > 0 and halted >= active:
                import asyncio as _asyncio
                try:
                    _asyncio.ensure_future(
                        self._cb.trigger_manual("per_strategy_all_halted")
                    )
                except RuntimeError:
                    pass  # No event loop — non-critical
                logger.warning(
                    "risk_check_12_global_cb_triggered",
                    halted=halted,
                    active=active,
                )

            if not self.per_strategy_cb.is_allowed(proposal.strategy_id):
                cb_state = self.per_strategy_cb.state(proposal.strategy_id).name
                RISK_REJECTIONS_TOTAL.labels(
                    check_number="12", reason="strategy_cb_halted"
                ).inc()
                return RiskCheckResult(
                    approved=False,
                    rejected_at_check=12,
                    reason=f"Strategy CB blocked: {proposal.strategy_id} state={cb_state}",
                )

        # US-278: Portfolio MDD advisory check (non-blocking — log only)
        if self.portfolio_risk is not None:
            try:
                mdd_result = self.portfolio_risk.check_mdd_breach()
                if mdd_result.get("portfolio_breach"):
                    logger.warning(
                        "risk_mdd_portfolio_breach: mdd_pct=%.2f%% strategy=%s",
                        mdd_result["portfolio_mdd_pct"] * 100,
                        proposal.strategy_id,
                    )
                for breach in mdd_result.get("strategy_breaches", []):
                    logger.warning(
                        "risk_mdd_strategy_breach: strategy=%s mdd_pct=%.2f%%",
                        breach["strategy_id"],
                        breach["mdd_pct"] * 100,
                    )
            except Exception as _exc:
                logger.debug("portfolio_risk.check_mdd_breach error: %s", _exc)

        logger.debug(
            "risk_check_approved",
            strategy=proposal.strategy_id,
            exchange=proposal.exchange_id,
            symbol=proposal.symbol,
            size=str(proposal.size),
        )
        return RiskCheckResult(approved=True)

    def check_trade_request(self, trade_request: Any, total_capital_usd: float = 120.0) -> bool:
        """
        BUG-22 fix: live.py path용 간소화 check — TradeRequest → TradeProposal 변환 후 check() 호출.

        live.py가 check_trade_request()를 hasattr로 탐색하므로 이 메서드가 존재하면 자동으로 사용됨.
        Check #0 (halt), #1 (position limit), #4 (circuit breaker), #8 (rollback cost)를 실행.
        Checks requiring full portfolio context (#2 drawdown, #3 exposure, #5 health, #7 volatility)는
        live.py의 flash_guard / symbol_cooldown / dedup_gate가 대신 처리하므로 skip.

        Args:
            trade_request: TradeRequest object with .legs, .strategy_id attributes
            total_capital_usd: Total engine capital (from capital.tiers config)
        """
        total_capital = Decimal(str(total_capital_usd))
        legs = getattr(trade_request, "legs", None) or []
        strategy_id = str(getattr(trade_request, "strategy_id", "unknown"))

        for leg in legs:
            price = getattr(leg, "price", None)
            size = getattr(leg, "size", None)
            if not price or not size or Decimal(str(size)) <= 0:
                continue
            side_raw = getattr(leg, "side", None)
            side_str = side_raw.value.upper() if hasattr(side_raw, "value") else str(side_raw).upper()

            proposal = TradeProposal(
                strategy_id=strategy_id,
                exchange_id=str(getattr(leg, "exchange_id", "unknown")),
                symbol=str(getattr(leg, "symbol", "UNKNOWN/USDT")),
                side=side_str,
                size=Decimal(str(size)),
                price=Decimal(str(price)),
                position_value=Decimal(str(price)) * Decimal(str(size)),
            )
            # Minimal PortfolioState: no existing positions, full health scores.
            # Drawdown/exposure/volatility checks skipped via empty containers.
            portfolio = PortfolioState(
                total_capital=total_capital,
                used_capital=Decimal("0"),
                current_drawdown_pct=Decimal("0"),
                total_exposure=Decimal("0"),
                position_sizes={},
                exchange_health_scores={proposal.exchange_id: Decimal("1.0")},
                volatility_1min={},
                volatility_24h={},
            )
            result = self.check(proposal, portfolio)
            if not result.approved:
                logger.warning(
                    "risk_check_trade_request_rejected check=%s reason=%s strategy=%s leg=%s",
                    result.rejected_at_check,
                    result.reason,
                    strategy_id,
                    proposal.exchange_id,
                )
                return False
        return True

    async def emergency_pause(self) -> None:
        """LiveGate continuous monitor FAIL 시 호출 — kill switch 활성화."""
        logger.critical("RiskGuardian.emergency_pause: LiveGate FAIL → kill switch halt")
        halt_local()
        self._halted = True
