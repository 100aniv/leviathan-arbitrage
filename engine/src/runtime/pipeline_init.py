"""Signal pipeline + strategies + DEX — Phase 4-5 main.py 모듈화 (2026-04-26).

Extracted from main.py (6 methods, ~520 LOC):
- init_signal_pipeline       (SignalGenerator + PriceHub + CostCalculator)
- init_strategies            (StrategyManager + 7 strategies)
- load_strategy_params       (engine/config/strategy_params.json)
- load_activation_disabled_ids (PHOENIX activation gate)
- register_default_strategies (cross_exchange/spot_futures/funding_rate/...)
- build_dex_adapter          (CexDex DEX 어댑터, 조건부)

각 함수는 ``engine: "Engine"`` 첫 인자.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.main import Engine

from src.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def init_signal_pipeline(engine: "Engine") -> None:
    from src.core.price_hub import PriceHub
    from src.core.signal import SignalConfig, SignalGenerator
    from src.core.stale_detector import StaleOrderbookDetector
    from src.friction.cost_calculator import CostCalculator
    from src.friction.fee_model import FeeModel
    from src.friction.slippage_model import CEXOrderbookSlippage

    engine._price_hub = PriceHub()

    # Build cost calculator with fee and slippage models
    try:
        fee_model = FeeModel()
        slippage_model = CEXOrderbookSlippage()
        engine._cost_calculator = CostCalculator(
            fee_model=fee_model,
            slippage_model=slippage_model,
        )
    except Exception as exc:
        logger.warning("CostCalculator init failed, using stub: %s", exc)
        engine._cost_calculator = None
        fee_model = None

    # WS-B: shared TCAAdaptiveFeedback — consumed by SignalGenerator (gate),
    # FF/FR strategies (dynamic min_spread), and live.py (record TCA observations).
    try:
        from src.friction.cost_feedback_loop import TCAAdaptiveFeedback
        from src.core.config_loader import get_config as _gc_cf
        _static_fb = Decimal(str(_gc_cf("strategy_filters.futures_min_spread_bps", default=27)))
        _margin = Decimal(str(_gc_cf("strategy_filters.dynamic_min_spread_margin_bps", default=5)))
        _funding = Decimal(str(_gc_cf("strategy_filters.dynamic_min_spread_funding_buffer_bps", default=5)))
        engine._cost_feedback: Any = TCAAdaptiveFeedback(
            window=100,
            fee_model=fee_model,
            min_samples=20,
            funding_buffer_bps=_funding,
            margin_bps=_margin,
            static_fallback_bps=_static_fb,
        )
        logger.info(
            "TCAAdaptiveFeedback initialized static_fallback=%sbps margin=%sbps funding_buf=%sbps",
            _static_fb, _margin, _funding,
        )
    except Exception as exc:
        engine._cost_feedback = None
        logger.warning("TCAAdaptiveFeedback init failed (non-fatal): %s", exc)

    _op = get_settings().operational
    min_edge_bps = _op.min_edge_bps
    max_spread_pct = _op.max_spread_pct
    cooldown_sec = _op.signal_cooldown_sec
    min_price_usd = _op.min_price_usd
    # US-326/327: load slippage_buffer + active_hours from strategy_params.json
    _ce_params = engine._load_strategy_params().get("cross_exchange", {})
    _slippage_buf = Decimal(str(_ce_params.get("slippage_buffer_bps", 0)))
    # US-327: MONITOR strategies get time-gated (KST 09-21); READY = always active
    _active_hours = (9, 21) if _ce_params.get("status") == "MONITOR" else None
    signal_config = SignalConfig(
        min_edge=Decimal(str(min_edge_bps)) / Decimal("10000"),  # bps → fraction
        max_spread_pct=Decimal(str(max_spread_pct)),
        cooldown_seconds=cooldown_sec,
        min_price_usd=min_price_usd,
        min_volume_usd=_op.signal_min_volume_usd,
        slippage_buffer_bps=_slippage_buf,  # US-326
        active_hours_kst=_active_hours,  # US-327
    )
    stale_detector = StaleOrderbookDetector(
        deviation_pct=_op.stale_cross_deviation_pct,
        blacklist_ttl_s=_op.stale_blacklist_ttl_s,
    )
    # US-131: RegimeDetector — try HMM first, fall back to threshold-based
    engine._regime_detector = None
    try:
        from src.tuning.regime_detector import HMMRegimeDetector
        engine._regime_detector = HMMRegimeDetector()
        logger.info("HMMRegimeDetector initialized")
    except (ImportError, Exception) as exc:
        logger.info("HMMRegimeDetector unavailable (%s), trying threshold-based", exc)
        try:
            from src.tuning.regime_detector import RegimeDetector
            engine._regime_detector = RegimeDetector()
            logger.info("RegimeDetector (threshold-based) initialized")
        except Exception as exc2:
            logger.warning("RegimeDetector init failed (non-fatal): %s", exc2)

    # US-131: ONNXSignalScorer — graceful fallback if onnxruntime not installed
    ml_scorer = None
    try:
        from src.ml.onnx_runtime import ONNXSignalScorer
        ml_scorer = ONNXSignalScorer()
        logger.info("ONNXSignalScorer initialized")
    except ImportError:
        logger.info("ONNXSignalScorer not available (onnxruntime not installed)")
    except Exception as exc:
        logger.warning("ONNXSignalScorer init failed (non-fatal): %s", exc)

    # US-253: MLFeaturePipeline — graceful fallback
    ml_feature_pipeline = None
    try:
        from src.ml.feature_pipeline import MLFeaturePipeline
        ml_feature_pipeline = MLFeaturePipeline()
        logger.info("MLFeaturePipeline initialized")
    except ImportError:
        logger.info("MLFeaturePipeline not available")
    except Exception as exc:
        logger.warning("MLFeaturePipeline init failed (non-fatal): %s", exc)

    # US-253: MLCanary staged rollout (10% → 50% → 100%) — graceful fallback
    ml_canary = None
    try:
        from src.ml.canary import MLCanary
        ml_canary = MLCanary(
            ml_scorer=ml_scorer,
            min_signals_to_promote=50,
            min_pnl_delta=0.0,
            auto_promote=True,
        )
        if ml_scorer is not None:
            ml_canary.start()  # begin at 10% ML traffic
        logger.info("MLCanary initialized (stage=%s)", ml_canary.stage.value)
    except ImportError:
        logger.info("MLCanary not available")
    except Exception as exc:
        logger.warning("MLCanary init failed (non-fatal): %s", exc)

    # US-174/255: PerStrategyAdaptiveThreshold for dynamic MIN_EDGE per strategy
    # Must be created BEFORE SignalGenerator so it can be injected
    try:
        from src.tuning.adaptive_threshold import PerStrategyAdaptiveThreshold
        engine._adaptive_threshold = PerStrategyAdaptiveThreshold(
            default_edge_bps=float(min_edge_bps),
        )
        logger.info("PerStrategyAdaptiveThreshold initialized (initial_edge_bps=%s)", min_edge_bps)
    except Exception as exc:
        logger.warning("AdaptiveThreshold init failed (non-fatal): %s", exc)

    # US-283: SlippageFeedbackCollector — per-exchange/pair slippage adjustment
    _slippage_fb_collector = None
    try:
        from src.friction.slippage_feedback import SlippageFeedbackCollector
        _slippage_fb_collector = SlippageFeedbackCollector()
        engine._slippage_fb_collector = _slippage_fb_collector
        logger.info("SlippageFeedbackCollector initialized")
    except Exception as exc:
        engine._slippage_fb_collector = None
        logger.warning("SlippageFeedbackCollector init failed (non-fatal): %s", exc)

    engine._signal_generator = SignalGenerator(
        price_hub=engine._price_hub,
        cost_calculator=engine._cost_calculator,
        config=signal_config,
        event_bus=engine._event_bus,
        stale_detector=stale_detector,
        regime_detector=engine._regime_detector,
        ml_scorer=ml_scorer,
        ml_feature_pipeline=ml_feature_pipeline,
        ml_canary=ml_canary,
        adaptive_threshold=engine._adaptive_threshold,
        slippage_feedback=_slippage_fb_collector,
        cost_feedback=getattr(engine, "_cost_feedback", None),  # WS-B
    )

    # US-170: TriangularScanner
    try:
        from src.core.triangular_scanner import TriangularScanner
        engine._triangular_scanner = TriangularScanner(
            min_profit_bps=Decimal(str(min_edge_bps)),
        )
        logger.info("TriangularScanner initialized (min_profit_bps=%s)", min_edge_bps)
    except Exception as exc:
        logger.warning("TriangularScanner init failed (non-fatal): %s", exc)

    logger.info(
        "Signal pipeline initialized min_edge_bps=%s max_spread_pct=%s stale_deviation_pct=%s"
        " regime_detector=%s ml_scorer=%s",
        min_edge_bps, max_spread_pct, get_settings().operational.stale_cross_deviation_pct,
        type(engine._regime_detector).__name__ if engine._regime_detector else "None",
        type(ml_scorer).__name__ if ml_scorer else "None",
    )

# ------------------------------------------------------------------
# Step 5: Strategies
# ------------------------------------------------------------------


async def init_strategies(engine: "Engine") -> None:
    from src.strategies.manager import StrategyManager

    # US-236: PositionRegistry for symbol-level lock in strategy dispatch
    try:
        from src.core.position_registry import PositionRegistry
        _position_registry = PositionRegistry()
        logger.info("PositionRegistry initialized for StrategyManager")
    except Exception as exc:
        logger.warning("PositionRegistry init failed (non-fatal): %s", exc)
        _position_registry = None

    engine._strategy_manager = StrategyManager(
        event_bus=engine._event_bus,
        consumer_name="manager-0",
        position_registry=_position_registry,
    )

    # Register default strategies based on available exchanges
    try:
        await engine._register_default_strategies()
    except Exception as exc:
        logger.warning("Strategy registration failed: %s", exc)

    logger.info("StrategyManager initialized with %d strategies",
                 len(engine._strategy_manager._strategies))
    from src.core.universe_matrix import UniverseMatrix
    engine._universe_matrix = UniverseMatrix()
    await engine._universe_matrix.build(engine._exchanges, engine._strategy_manager._strategies.values())


def load_strategy_params(engine: "Engine") -> dict:
    """Load tuned strategy parameters from config/strategy_params.json."""
    import json
    import pathlib
    params_path = pathlib.Path(__file__).parent.parent / "config" / "strategy_params.json"
    if not params_path.exists():
        logger.info("No tuned strategy params found at %s, using defaults", params_path)
        return {}
    try:
        with open(params_path) as f:
            params = json.load(f)
        logger.info("Loaded tuned strategy params from %s", params_path)
        return params
    except Exception as exc:
        logger.warning("Failed to load strategy params: %s", exc)
        return {}


def load_activation_disabled_ids(engine: "Engine") -> set[str]:
    """Load disabled strategy IDs from strategy_activation.json (extracted for testability)."""
    import pathlib
    _activation_path = pathlib.Path(__file__).parent.parent / "config" / "strategy_activation.json"
    try:
        if _activation_path.exists():
            with open(_activation_path) as _f:
                _activation = json.load(_f)
            _disabled = set(_activation.get("disabled_strategies", []))
            if _disabled:
                logger.info("Skipping disabled strategies: %s", _disabled)
            return _disabled
    except Exception as _exc:
        logger.warning("Failed to load strategy_activation.json: %s", _exc)
    return set()


async def register_default_strategies(engine: "Engine") -> None:
    """Register all 8 available strategies with tuned parameters."""
    from src.core.latency_tracker import LatencyTracker
    from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy
    from src.strategies.spot_futures import SpotFuturesConfig, SpotFuturesStrategy
    from src.strategies.futures_futures import FuturesFuturesConfig, FuturesFuturesStrategy
    from src.strategies.triangular import TriangularConfig, TriangularStrategy
    from src.strategies.funding_rate import FundingRateConfig, FundingRateStrategy
    from src.strategies.statistical_arb import StatisticalArbStrategy
    # latency_arb merged into cross_exchange (US-194) — no separate import needed

    # Use a simple stub if CostCalculator didn't initialize
    cost_calc = engine._cost_calculator
    if cost_calc is None:
        cost_calc = _StubCostCalculator()

    # Shared latency tracker for latency_boost mode in CrossExchangeStrategy (US-194)
    engine._latency_tracker = LatencyTracker()

    # Load tuned parameters (READY/MONITOR strategies only)
    tuned = engine._load_strategy_params()

    # Phase H-Final: Dynamic capital-based sizing
    # All position/depth limits are % of capital, not fixed USD
    from src.core.config import load_engine_config
    _ecfg = load_engine_config()
    _cap_cfg = _ecfg.get("capital", {})
    _tier = _cap_cfg.get("tier", "alpha")
    _allocation_mode = _cap_cfg.get("allocation_mode", "tiers")
    _tier_initial_usd = Decimal(str(
        _cap_cfg.get("tiers", {}).get(_tier, {}).get("initial_usd", 70)
    ))
    if _allocation_mode == "percentage":
        # BUG-148: percentage mode should derive capital from live balances.
        # Attempt to read balances right here; fall back to tier default on any error.
        _live_total_usd: Decimal = Decimal("0")
        try:
            if hasattr(engine, "_exchanges") and engine._exchanges:
                # FX rate for KRW conversion
                from src.core.config_loader import get_config as _gc_cap
                _fx_cap = float(_gc_cap("strategy_filters.krw_usdt_rate", default=0.000676))
                _KRW_IDS = {"upbit", "bithumb", "coinone"}
                for _ex_name, _ex_adapter in engine._exchanges.items():
                    try:
                        _bals = await _ex_adapter.get_balances()
                    except Exception:
                        continue
                    _usdt_bal = _bals.get("USDT")
                    if _usdt_bal:
                        _live_total_usd += Decimal(str(_usdt_bal.total))
                    if _ex_name.lower() in _KRW_IDS or any(k in _ex_name.lower() for k in _KRW_IDS):
                        _krw_bal = _bals.get("KRW")
                        if _krw_bal:
                            _live_total_usd += Decimal(str(_krw_bal.total)) * Decimal(str(_fx_cap))
        except Exception as _cap_exc:
            logger.warning("capital.balance_probe_failed err=%s", _cap_exc)
        if _live_total_usd > 0:
            _capital_usd = _live_total_usd
            logger.info(
                "capital.allocation_mode=percentage capital=$%.2f (live balance, tier fallback=$%.0f)",
                float(_capital_usd), float(_tier_initial_usd),
            )
        else:
            _capital_usd = _tier_initial_usd
            logger.info(
                "capital.allocation_mode=percentage reserve_pct=%s strategies=%s "
                "(balance unavailable — fallback=$%.0f)",
                _cap_cfg.get("reserve_pct", 20),
                list(_cap_cfg.get("strategies", {}).keys()),
                _capital_usd,
            )
    else:
        _capital_usd = _tier_initial_usd
    _risk_cfg = _ecfg.get("dynamic_risk", {})
    _base_pos_pct = Decimal(str(_risk_cfg.get("base_position_pct", 3.0))) / Decimal("100")
    _strategy_allocs = _cap_cfg.get("strategies", {})
    _book_depth_usd = max(Decimal("1"), _capital_usd * Decimal("0.01"))  # 1% of capital, min $1

    _max_pos_usd = _capital_usd * _base_pos_pct  # capital × base_position_pct (config 기반)
    # BUG-79: Wire allocation_pct → per-strategy capital cap.
    # Each strategy gets (capital × allocation_pct/100) as its max total exposure.
    # Per-trade size = min(base_position_pct of total, strategy_capital_cap).
    _reserve_pct = Decimal(str(_cap_cfg.get("reserve_pct", 20))) / Decimal("100")
    _usable_capital = _capital_usd * (Decimal("1") - _reserve_pct)

    def _strategy_max_pos(strategy_key: str) -> Decimal:
        """Per-strategy position size from allocation_pct."""
        alloc = _strategy_allocs.get(strategy_key, {})
        alloc_pct = Decimal(str(alloc.get("allocation_pct", 25))) / Decimal("100")
        strategy_cap = _usable_capital * alloc_pct  # strategy's total capital
        # Per-trade: min of global per_trade or strategy capital
        return min(_max_pos_usd, strategy_cap)

    logger.info(
        "Strategy sizing: capital=$%.0f usable=$%.0f (reserve=%.0f%%) tier=%s per_trade=$%.2f "
        "allocs={%s}",
        _capital_usd, _usable_capital, float(_reserve_pct * 100), _tier, float(_max_pos_usd),
        ", ".join(f"{k}:{v.get('allocation_pct')}%" for k, v in _strategy_allocs.items()),
    )

    # Build strategy configs from tuned params + dynamic capital sizing
    from src.core.config_loader import get_config
    sf_p = tuned.get("spot_futures", {})
    _sf_max_hold_s = get_config("strategy_filters.spot_futures_max_hold_seconds", default=1800)
    # BUG-110: always create sf_config so max_position_size is enforced.
    # Previously gated on status ∈ (READY, MONITOR) → None fallback used SF
    # default max_position_size=50000 → risk_guardian rejected all trades
    # (notional=$100 vs max=$12.60 = 10.5% of $120 capital).
    sf_config = SpotFuturesConfig(
        min_basis_bps=Decimal(str(sf_p.get("min_basis_bps", 15))),
        max_position_size=_strategy_max_pos("spot_futures"),
        max_holding_hours=_sf_max_hold_s / 3600.0,
    )

    fr_p = tuned.get("funding_rate", {})
    # Use percentage-based _max_pos_usd (capital × per_trade_pct %).
    # _MIN_NOTIONAL_USD in FundingRateStrategy is $5 (exchange min), so _max_pos_usd >= $5 needed.
    # With capital=$120 and per_trade_pct=5%: _max_pos_usd=$6 > $5 → OK.
    fr_config = FundingRateConfig(
        min_funding_diff_bps=Decimal(str(fr_p.get("min_funding_diff_bps", 5))),
        max_position_size=_strategy_max_pos("funding_rate"),
        enable_ou_filter=fr_p.get("enable_ou_filter", True),
    ) if fr_p.get("status") in ("READY", "MONITOR") else None

    ce_p = tuned.get("cross_exchange", {})
    # BUG-219: always create ce_config so max_position_size is enforced.
    # Previously gated on status ∈ (READY, MONITOR) — with strategy_params
    # status="DISABLED_PHASE2", ce_config=None → CrossExchangeStrategy used
    # PHOENIX default max_position_size=50000 → XE-KRW signals produced
    # notional ~$5300 which the RiskGuardian rejected as trade_too_large
    # (max=$12.60 = 5% of capital). Same shape as BUG-110 for sf_config.
    # XE-KRW (Upbit/Bithumb/Coinone) signals pass min_spread filter (5-99bps
    # vs 10bps threshold) so 100% landed on the guardian → zero orders placed.
    ce_config = CrossExchangeConfig(
        min_spread_bps=Decimal(str(ce_p.get("min_spread_bps", 10))),
        max_position_size=_strategy_max_pos("cross_exchange"),
        min_book_depth_usd=_book_depth_usd,
    )

    ff_p = tuned.get("futures_futures", {})
    from src.core.config_loader import get_config as _get_config
    _ff_excluded = _get_config("strategy_filters.futures_excluded_symbols", default=[])
    # BUG-27: FF max_position_size must NOT use percentage-based _max_pos_usd.
    # Use fixed notional BUT capped by allocation_pct (BUG-79).
    _ff_fixed = Decimal(str(_get_config("strategy_filters.futures_futures_max_position_usd", default=12)))
    _ff_alloc_cap = _strategy_max_pos("futures_futures")
    _ff_max_pos = min(_ff_fixed, _ff_alloc_cap) if _ff_alloc_cap > 0 else _ff_fixed
    ff_config = FuturesFuturesConfig(
        # engine.json strategy_filters.futures_min_spread_bps is the SOLE source of truth.
        min_spread_bps=Decimal(str(_get_config("strategy_filters.futures_min_spread_bps", default=27))),
        max_position_size=_ff_max_pos,
        min_book_depth_usd=_book_depth_usd,
        excluded_symbols=list(_ff_excluded),
        adaptive_static_entry_bps=Decimal(str(_get_config("strategy_filters.futures_adaptive_static_entry_bps", default=50))),
        # BUG-91: max_hold_seconds was NOT passed → Pydantic default 1800s used instead of 300s.
        # Positions held 30min → exchange auto-close → ghost on timeout exit.
        max_hold_seconds=float(_get_config("strategy_filters.futures_max_hold_seconds", default=300)),
    ) if ff_p.get("status") in ("READY", "MONITOR") else None

    tri_p = tuned.get("triangular", {})
    tri_config = TriangularConfig(
        min_profit_bps=Decimal(str(tri_p.get("min_profit_bps", 10))),
        max_position_usdt=_max_pos_usd,
    ) if tri_p.get("status") in ("READY", "MONITOR") else None

    # Load disabled strategies from strategy_activation.json
    _disabled_ids = engine._load_activation_disabled_ids()

    strategies = [
        s for s in [
            CrossExchangeStrategy("cross_exchange_v1", cost_calc, config=ce_config,
                                  latency_tracker=engine._latency_tracker,
                                  regime_detector=engine._regime_detector,
                                  exchange_registry=engine._exchanges),
            SpotFuturesStrategy("spot_futures_v1", cost_calc, config=sf_config,
                                regime_detector=engine._regime_detector),
            FuturesFuturesStrategy("futures_futures_v1", cost_calc, config=ff_config,
                                   regime_detector=engine._regime_detector,
                                   cost_feedback=getattr(engine, "_cost_feedback", None)),
            TriangularStrategy("triangular_v1", cost_calc, config=tri_config,
                               regime_detector=engine._regime_detector),
            FundingRateStrategy("funding_rate_v1", cost_calc, config=fr_config,
                                regime_detector=engine._regime_detector,
                                cost_feedback=getattr(engine, "_cost_feedback", None)),
            *(
                [StatisticalArbStrategy("statistical_arb_v1", cost_calc,
                                        regime_detector=engine._regime_detector)]
                if tuned.get("statistical_arb", {}).get("status") in ("READY", "MONITOR")
                else []
            ),
        ]
        if s.strategy_id not in _disabled_ids
    ]

    # CexDex requires a DEXAdapter — register only if configured
    try:
        from src.strategies.cex_dex import CexDexStrategy
        dex_adapter = engine._build_dex_adapter()
        if dex_adapter is not None:
            dex_cost = None
            try:
                from src.friction.dex_cost import DEXCostCalculator
                dex_cost = DEXCostCalculator()
            except Exception:
                pass
            strategies.append(
                CexDexStrategy(
                    "cex_dex_v1", cost_calc, dex_adapter,
                    cex_exchange_id=list(engine._exchanges.keys())[0] if engine._exchanges else "binance",
                    symbol="BTC/USDT",
                    dex_cost_calculator=dex_cost,
                )
            )
    except Exception as exc:
        logger.info("CexDex strategy not registered (no DEX adapter): %s", exc)

    for strategy in strategies:
        engine._strategy_manager.register(strategy)

    tuned_count = sum(1 for s in ["spot_futures", "funding_rate", "cross_exchange",
                                   "futures_futures", "triangular"] if tuned.get(s, {}).get("status") in ("READY", "MONITOR"))
    logger.info("Registered %d strategies (%d with tuned params)", len(strategies), tuned_count)


def build_dex_adapter(engine: "Engine"):
    """Build DEX adapter if DEX configuration is available. Returns None if not configured.

    US-242: When DEX_RPC_URL is unset but SHADOW_MOCK_DEX=true, returns a
    MockDEXAdapter that derives prices from CEX mid-prices.
    """
    _op = get_settings().operational
    dex_rpc = _op.dex_rpc_url
    if not dex_rpc:
        # US-242: Check for mock DEX adapter in shadow mode
        if _op.paper_mock_dex:
            try:
                from src.dex.mock_adapter import MockDEXAdapter
                adapter = MockDEXAdapter()
                logger.info("MockDEXAdapter initialized (SHADOW_MOCK_DEX=true)")
                return adapter
            except Exception as exc:
                logger.warning("MockDEXAdapter init failed: %s", exc)
        return None
    pool = _op.dex_pool_address
    if not pool:
        logger.info("DEX_RPC_URL set but DEX_POOL_ADDRESS missing")
        return None
    try:
        from src.infra.dex.uniswap_v3 import UniswapV3Adapter, UniswapV3Config
        config = UniswapV3Config(rpc_url=dex_rpc, pool_address=pool)
        adapter = UniswapV3Adapter(config)
        logger.info("UniswapV3Adapter initialized: pool=%s", pool[:10] + "...")
        return adapter
    except Exception as exc:
        logger.warning("DEX adapter init failed: %s", exc)
        return None

# ------------------------------------------------------------------
# Step 6: Risk Management
# ------------------------------------------------------------------


