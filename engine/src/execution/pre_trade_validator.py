"""Phoenix Path-B Day-2 — PreTradeValidator pipeline.

Extracts the 270-line pre-trade if-ladder from `src/modes/live.py` into a
standalone, injectable validator. Every gate returns a typed
:class:`ValidationResult` with a stable :class:`~src.core.reason_codes.ReasonCode`.

Design goals
------------
1. **No behaviour change** — each gate is a literal port of the inline logic
   in ``LiveMode._execute_trade_request`` (pre-Day-2). Bugs like BUG-78
   (margin soft block keeps _open_positions) and BUG-79 (dedup close path
   does NOT notify rollback) are preserved as-is.
2. **Controlled vocabulary** — every reject path emits a
   :class:`ReasonCode`, increments ``leviathan_signal_rejected_total``, and
   logs ``live_mode.rejected_by_<code>`` at INFO (closes BUG-227 silent
   rollback regression class).
3. **Fail-fast order** — first failing gate short-circuits the pipeline. The
   order mirrors the original ladder so e.g. ``KILL_SWITCH_HALT`` wins over
   ``STRATEGY_COOLDOWN`` for a halted strategy.
4. **Injectable dependencies** — everything the validator touches is passed
   in via ``__init__`` so unit tests can build a validator with fakes.

Reject-but-special-case flag
----------------------------
Two historical rules require the caller (live.py) to NOT fire
``_notify_pre_exec_rollback`` on rejection:

* **BUG-78 margin guard**: leaving the phantom position in ``_open_positions``
  acts as a soft block; clearing it would permit a hot retry loop.
* **BUG-79 dedup close**: an exit already in flight moved the position to
  ``_pending_exits``; rollback would thrash the state machine.

Those paths set ``ValidationResult.skip_rollback_notify = True`` and the
caller inspects it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable, Mapping

from src.core.reason_codes import ReasonCode
from src.strategies.base import TradeLeg, TradeRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Outcome of a pre-trade validation run.

    Attributes
    ----------
    approved:
        ``True`` if the trade may proceed, ``False`` if any gate rejected it.
    reason_code:
        The specific :class:`ReasonCode` that rejected (None when approved).
    detail:
        Human-readable context — used for structured logging. For the
        auto-bump success path this is ``"bumped"``.
    metric_labels:
        Label map passed through to Prometheus (exchange / symbol / etc.).
    skip_rollback_notify:
        BUG-78 / BUG-79 — caller MUST NOT call ``_notify_pre_exec_rollback``
        when True. Applies to margin soft block + dedup-close special cases.
    """

    approved: bool
    reason_code: ReasonCode | None = None
    detail: str | None = None
    metric_labels: dict[str, str] = field(default_factory=dict)
    skip_rollback_notify: bool = False


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class PreTradeValidator:
    """Runs 11 + bump pre-trade gates and emits typed ValidationResults."""

    # BUG-74: block new futures entries below this free margin. Mirrors the
    # literal constant in live.py so behaviour stays frozen during extraction.
    MIN_MARGIN_ENTRY_USD: float = 3.0

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        *,
        strategy_filter: frozenset[str] | None,
        strategy_disable_until: dict[str, float],
        kill_switch: Any | None,
        circuit_breaker: Any | None,
        rate_buckets: dict[str, Any] | None,
        flash_guard: Any | None,
        risk_guardian: Any | None,
        dedup_gate: Any,
        symbol_last_trade: dict[str, float],
        symbol_cooldown_s: float,
        cached_margin: dict[str, Decimal],
        min_notional_registry: Any,
        get_config: Callable[..., Any],
        total_capital_usd: float,
        max_session_loss_usd: float,
        session_loss_supplier: Callable[[], float],
        build_collision_key: Callable[[TradeRequest], str],
        is_reduceonly_request: Callable[[TradeRequest], bool],
        halt_local: Callable[[], None],
        telegram: Any | None = None,
        notify_session_loss: Callable[[float, float], Awaitable[None]] | None = None,
        clear_pending_entry: Callable[[str, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._strategy_filter = strategy_filter
        self._strategy_disable_until = strategy_disable_until
        self._kill_switch = kill_switch
        self._circuit_breaker = circuit_breaker
        self._rate_buckets = rate_buckets if rate_buckets is not None else {}
        self._has_rate_limiter = rate_buckets is not None
        self._flash_guard = flash_guard
        self._risk_guardian = risk_guardian
        self._dedup_gate = dedup_gate
        self._symbol_last_trade = symbol_last_trade
        self._symbol_cooldown_s = float(symbol_cooldown_s)
        self._cached_margin = cached_margin
        self._min_notional_registry = min_notional_registry
        self._get_config = get_config
        self._total_capital_usd = float(total_capital_usd)
        self._max_session_loss_usd = float(max_session_loss_usd)
        self._session_loss_supplier = session_loss_supplier
        self._build_collision_key = build_collision_key
        self._is_reduceonly_request = is_reduceonly_request
        self._halt_local = halt_local
        self._telegram = telegram
        self._notify_session_loss = notify_session_loss
        self._clear_pending_entry = clear_pending_entry
        self._clock = clock

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def validate(
        self,
        trade_request: TradeRequest,
        strategy_id: str,
        context: Mapping[str, Any] | None = None,
    ) -> ValidationResult:
        """Run all gates fail-fast and return the first rejection (or approval).

        ``context`` is intentionally opaque so callers can pass per-call extras
        (strat_stats, tracing IDs) without widening the validator surface.
        When the caller passes a mutable ``dict``, gate side-effects
        (``risk_blocked``, ``margin_blocked``) are written back so the caller
        can mirror legacy stats counters.
        """
        if isinstance(context, dict):
            ctx = context
        else:
            ctx = dict(context or {})
        is_close = self._is_reduceonly_request(trade_request)

        # Ordered gates. Each returns a ValidationResult or None (None == pass).
        # Using method list preserves the original ladder's fail-fast order.
        gates: list[
            Callable[..., Awaitable[ValidationResult | None] | ValidationResult | None]
        ] = [
            self._check_strategy_filter,
            self._check_strategy_cooldown,
            self._check_kill_switch,
            self._check_circuit_breaker,
            self._check_rate_limiter,
            self._check_flash_guard,
            self._check_session_loss,
            self._check_risk_guardian,
            self._check_symbol_cooldown,
            self._check_margin_guard,
            self._check_dedup,
            self._check_notional_with_bump,
        ]

        for gate in gates:
            result = gate(trade_request, strategy_id, is_close, ctx)
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[assignment]
            if result is None:
                continue
            # Final result from gate — either approved (auto-bump success) or rejected.
            if result.approved:
                # Auto-bump passed; record label and continue pipeline.
                if result.detail == "bumped":
                    continue
                return result
            self._record_rejection(result, strategy_id, trade_request)
            return result

        return ValidationResult(approved=True)

    # ------------------------------------------------------------------
    # Gates — each matches the original live.py logic line-for-line
    # ------------------------------------------------------------------

    def _check_strategy_filter(
        self,
        trade_request: TradeRequest,
        sid: str,
        is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        """Allowlist gate. Exit orders bypass (MEDIUM-1 — stuck positions)."""
        if self._strategy_filter is None or sid in self._strategy_filter:
            return None
        if is_close:
            logger.debug("live_mode.strategy_filter_bypassed_exit strategy=%s", sid)
            return None
        return ValidationResult(
            approved=False,
            reason_code=ReasonCode.STRATEGY_FILTERED,
            detail=f"strategy={sid}",
        )

    def _check_strategy_cooldown(
        self,
        trade_request: TradeRequest,
        sid: str,
        is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        """US-164 per-strategy loss cooldown. Exit orders bypass."""
        until = self._strategy_disable_until.get(sid)
        if until is None:
            return None
        if self._clock() >= until:
            # expired — purge + allow
            self._strategy_disable_until.pop(sid, None)
            return None
        if is_close:
            return None
        return ValidationResult(
            approved=False,
            reason_code=ReasonCode.STRATEGY_COOLDOWN,
            detail=f"strategy={sid}",
        )

    def _check_kill_switch(
        self,
        _tr: TradeRequest,
        _sid: str,
        _is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        if self._kill_switch is None:
            return None
        if not hasattr(self._kill_switch, "is_halted"):
            return None
        if not self._kill_switch.is_halted():
            return None
        return ValidationResult(
            approved=False,
            reason_code=ReasonCode.KILL_SWITCH_HALT,
            detail="halt flag set",
        )

    def _check_circuit_breaker(
        self,
        _tr: TradeRequest,
        sid: str,
        _is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        if self._circuit_breaker is None:
            return None
        try:
            if hasattr(self._circuit_breaker, "is_open") and self._circuit_breaker.is_open():
                return ValidationResult(
                    approved=False,
                    reason_code=ReasonCode.CIRCUIT_BREAKER_OPEN,
                    detail=f"strategy={sid}",
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("live_mode.circuit_breaker_check_error: %s", exc)
        return None

    def _check_rate_limiter(
        self,
        trade_request: TradeRequest,
        sid: str,
        _is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        if not self._has_rate_limiter:
            return None
        # Lazy import mirrors live.py behaviour (avoids hard dependency if import fails).
        try:
            from src.infra.exchange.rate_limiter import TokenBucket  # noqa: PLC0415
        except Exception:  # pragma: no cover - rate_limiter always importable in prod
            return None
        for leg in trade_request.legs:
            ex = leg.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
            if ex not in self._rate_buckets:
                self._rate_buckets[ex] = TokenBucket(rate=5.0, capacity=10.0)
            if not self._rate_buckets[ex].try_acquire():
                return ValidationResult(
                    approved=False,
                    reason_code=ReasonCode.RATE_LIMITED,
                    detail=f"exchange={ex} strategy={sid}",
                    metric_labels={"exchange": ex},
                )
        return None

    def _check_flash_guard(
        self,
        trade_request: TradeRequest,
        sid: str,
        _is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        if self._flash_guard is None:
            return None
        try:
            if hasattr(self._flash_guard, "check"):
                blocked = self._flash_guard.check(trade_request)
                if blocked:
                    return ValidationResult(
                        approved=False,
                        reason_code=ReasonCode.FLASH_GUARD_BLOCKED,
                        detail=f"strategy={sid}",
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("live_mode.flash_guard_check_error: %s", exc)
        return None

    async def _check_session_loss(
        self,
        trade_request: TradeRequest,
        sid: str,
        _is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        """engine.json live.max_daily_loss_pct. Fires halt_local() on breach."""
        loss = float(self._session_loss_supplier())
        if loss < self._max_session_loss_usd:
            return None
        try:
            self._halt_local()
        except Exception as exc:  # noqa: BLE001
            logger.warning("live_mode.session_loss_halt_failed: %s", exc)
        logger.critical(
            "live_mode.session_loss_limit_exceeded loss=%.2f limit=%.2f — HALT",
            loss,
            self._max_session_loss_usd,
        )
        if self._notify_session_loss is not None:
            try:
                await self._notify_session_loss(loss, self._max_session_loss_usd)
            except Exception:  # noqa: BLE001
                pass
        return ValidationResult(
            approved=False,
            reason_code=ReasonCode.SESSION_LOSS_LIMIT,
            detail=f"loss=${loss:.2f} limit=${self._max_session_loss_usd:.2f}",
            metric_labels={"strategy": sid},
        )

    def _check_risk_guardian(
        self,
        trade_request: TradeRequest,
        sid: str,
        _is_close: bool,
        ctx: dict[str, Any],
    ) -> ValidationResult | None:
        if self._risk_guardian is None:
            return None
        try:
            approved = True
            if hasattr(self._risk_guardian, "check_trade_request"):
                approved = self._risk_guardian.check_trade_request(
                    trade_request, self._total_capital_usd
                )
            elif hasattr(self._risk_guardian, "approve"):
                approved = self._risk_guardian.approve(trade_request)
            if approved:
                return None
            ctx["risk_blocked"] = True  # caller bumps trades_risk_blocked on this flag
            sym = trade_request.legs[0].symbol if trade_request.legs else "?"
            exs = "/".join(_leg.exchange_id for _leg in trade_request.legs)
            return ValidationResult(
                approved=False,
                reason_code=ReasonCode.RISK_GUARDIAN_REJECTED,
                detail=f"strategy={sid} symbol={sym} legs={exs}",
                metric_labels={"strategy": sid},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("live_mode.risk_check_error: %s", exc)
            return None

    def _check_symbol_cooldown(
        self,
        trade_request: TradeRequest,
        sid: str,
        is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        """Per-symbol burst cooldown. Exit orders bypass (BUG-36)."""
        sym_keys = [l.symbol for l in trade_request.legs if l.symbol]
        if not sym_keys or is_close:
            return None
        now = self._clock()
        for sk in sym_keys:
            last = self._symbol_last_trade.get(sk, 0.0)
            if now - last < self._symbol_cooldown_s:
                return ValidationResult(
                    approved=False,
                    reason_code=ReasonCode.SYMBOL_COOLDOWN,
                    detail=(
                        f"symbol={sk} strategy={sid} cooldown_s={self._symbol_cooldown_s:.0f} "
                        f"remaining={self._symbol_cooldown_s - (now - last):.1f}"
                    ),
                    metric_labels={"symbol": sk},
                )
        # No cooldown hit — stamp now. Mirrors live.py two-loop pattern so
        # partially-cooldown-blocked requests don't poison the other legs.
        for sk in sym_keys:
            self._symbol_last_trade[sk] = now
        return None

    def _check_margin_guard(
        self,
        trade_request: TradeRequest,
        sid: str,
        is_close: bool,
        ctx: dict[str, Any],
    ) -> ValidationResult | None:
        """BUG-74 margin guard — block entries on margin-exhausted futures."""
        if is_close:
            return None
        for leg in trade_request.legs:
            if leg.exchange_id and "futures" in leg.exchange_id:
                cached = float(self._cached_margin.get(leg.exchange_id, float("inf")))
                if cached < self.MIN_MARGIN_ENTRY_USD:
                    ctx["margin_blocked"] = True  # caller bumps trades_margin_blocked
                    # BUG-96 GAP#1: clear pre-exec pending state only.
                    if self._clear_pending_entry is not None:
                        for mg_leg in trade_request.legs:
                            if mg_leg.symbol:
                                try:
                                    self._clear_pending_entry(sid, mg_leg.symbol)
                                except Exception as mg_err:  # noqa: BLE001
                                    logger.debug(
                                        "live_mode.margin_guard_clear_failed sym=%s err=%s",
                                        mg_leg.symbol,
                                        mg_err,
                                    )
                    # BUG-78: do NOT rollback — leave phantom as soft block.
                    return ValidationResult(
                        approved=False,
                        reason_code=ReasonCode.MARGIN_INSUFFICIENT,
                        detail=(
                            f"ex={leg.exchange_id} margin={cached:.2f} < "
                            f"{self.MIN_MARGIN_ENTRY_USD:.2f}"
                        ),
                        metric_labels={"exchange": leg.exchange_id},
                        skip_rollback_notify=True,
                    )
        return None

    async def _check_dedup(
        self,
        trade_request: TradeRequest,
        sid: str,
        is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        collision_key = self._build_collision_key(trade_request)
        passed = await self._dedup_gate.check_and_register(collision_key)
        if passed:
            return None
        if is_close:
            # BUG-79: exit already in flight — do NOT notify rollback.
            logger.warning(
                "live_mode.dedup_blocked_close key=%s — first exit still in flight",
                collision_key,
            )
            return ValidationResult(
                approved=False,
                reason_code=ReasonCode.DEDUP_COLLISION,
                detail=f"close key={collision_key}",
                skip_rollback_notify=True,
            )
        return ValidationResult(
            approved=False,
            reason_code=ReasonCode.DEDUP_COLLISION,
            detail=f"key={collision_key} strategy={sid}",
        )

    async def _check_notional_with_bump(
        self,
        trade_request: TradeRequest,
        sid: str,
        _is_close: bool,
        _ctx: dict[str, Any],
    ) -> ValidationResult | None:
        """BUG-228c auto-bump + BUG-220 risk cap enforcement.

        Returns:
            * ``None`` — nothing needed bumping.
            * ``ValidationResult(approved=True, detail="bumped")`` — legs rewritten.
            * ``ValidationResult(approved=False, NOTIONAL_BUMP_EXCEEDS_RISK)`` — cap breach.
        """
        USD_QUOTES = {"USDT", "USDC", "USD", "BUSD", "DAI", "KRW"}
        # (leg, required_min, current_notional, bumped_size)
        small_legs: list[tuple[TradeLeg, Decimal, Decimal, Decimal]] = []
        for leg in trade_request.legs:
            if not (leg.price and leg.price > 0):
                continue
            quote = leg.symbol.split("/")[-1].upper() if "/" in leg.symbol else ""
            if quote not in USD_QUOTES:
                continue
            required = await self._min_notional_registry.get(leg.exchange_id, leg.symbol)
            current = leg.size * leg.price
            if current < required:
                bumped = (required / leg.price).quantize(Decimal("0.00000001"))
                small_legs.append((leg, required, current, bumped))

        if not small_legs:
            return None

        # Risk cap check
        risk_cap_usd = Decimal(str(self._total_capital_usd)) * (
            Decimal(str(self._get_config("risk.max_position_pct", default=6)))
            / Decimal("100")
        )
        max_bumped_notional = max(entry[3] * entry[0].price for entry in small_legs)
        if max_bumped_notional > risk_cap_usd:
            try:
                from src.infra.metrics import SIGNALS_REJECTED_NOTIONAL as _m  # noqa: PLC0415
                for entry in small_legs:
                    _m.labels(exchange=entry[0].exchange_id, symbol=entry[0].symbol).inc()
            except Exception:  # noqa: BLE001
                pass
            logger.info(
                "live_mode.min_notional_bump_exceeds_risk_cap strategy=%s "
                "max_bumped=$%.2f risk_cap=$%.2f legs=[%s]",
                sid,
                float(max_bumped_notional),
                float(risk_cap_usd),
                ",".join(
                    f"{e[0].exchange_id}:{e[0].symbol}:current=${float(e[2]):.2f}<min=${float(e[1]):.2f}"
                    for e in small_legs
                ),
            )
            return ValidationResult(
                approved=False,
                reason_code=ReasonCode.NOTIONAL_BUMP_EXCEEDS_RISK,
                detail=f"max_bumped=${float(max_bumped_notional):.2f} cap=${float(risk_cap_usd):.2f}",
                metric_labels={"strategy": sid},
            )

        # Auto-bump: rewrite leg sizes in-place (request is local to this caller).
        for leg, required, current, bumped in small_legs:
            logger.info(
                "live_mode.min_notional_bump_applied strategy=%s exchange=%s "
                "symbol=%s size=%s->%s notional=$%.2f->$%.2f",
                sid,
                leg.exchange_id,
                leg.symbol,
                leg.size,
                bumped,
                float(current),
                float(bumped * leg.price),
            )
            try:
                from src.infra.metrics import (  # noqa: PLC0415
                    SIGNAL_AUTO_BUMPED_TOTAL,
                    SIGNALS_AUTO_BUMPED_NOTIONAL as _mb,
                )
                _mb.labels(exchange=leg.exchange_id, symbol=leg.symbol).inc()
                SIGNAL_AUTO_BUMPED_TOTAL.labels(exchange=leg.exchange_id).inc()
            except Exception:  # noqa: BLE001
                pass
            leg.size = bumped
        return ValidationResult(approved=True, detail="bumped")

    # ------------------------------------------------------------------
    # Rejection emission — unified metric + log
    # ------------------------------------------------------------------

    def _record_rejection(
        self,
        result: ValidationResult,
        sid: str,
        trade_request: TradeRequest,
    ) -> None:
        """Increment Prometheus + emit structured INFO log. BUG-227 closes here."""
        try:
            from src.infra.metrics import SIGNAL_REJECTED_TOTAL  # noqa: PLC0415
            SIGNAL_REJECTED_TOTAL.labels(
                reason_code=result.reason_code.value if result.reason_code else "unknown",
                strategy=sid,
            ).inc()
        except Exception:  # noqa: BLE001
            pass
        if result.reason_code is not None:
            log_tag = f"live_mode.rejected_by_{result.reason_code.value}"
            logger.info(
                "%s strategy=%s legs=%d detail=%s",
                log_tag,
                sid,
                len(trade_request.legs),
                result.detail or "",
            )
