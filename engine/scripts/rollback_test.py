"""AtomicExecutor rollback path validation.

Verifies the cross-exchange rollback protocol:
  leg1: real unfillable limit order (price × 0.50)
  leg2: forced failure (MockAdapter raises exception)
  rollback: AtomicExecutor._rollback_order() cancels leg1
  halt check: is_halted() must be False after successful rollback

Usage::

    cd engine
    python scripts/rollback_test.py --exchange binance

Results are appended to .omc/state/plumbing-results.json (phase1_5_rollback section).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import traceback
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

_REPO_ROOT = _ENGINE_ROOT.parent
_ENV_FILE = _REPO_ROOT / ".env"


def _load_env(path: Path) -> None:
    """Parse KEY=VALUE lines from .env into os.environ (no override)."""
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(str(path), override=False)
        return
    except ImportError:
        pass
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env(_ENV_FILE)

# ---------------------------------------------------------------------------
# Engine imports
# ---------------------------------------------------------------------------
from src.core.models import Order, OrderBook, OrderSide, OrderType, Trade  # noqa: E402
from src.execution.executor import AtomicExecutor, ExecutionConfig, ExecutionStatus  # noqa: E402
from src.infra.exchange import create_native_adapter  # noqa: E402
from src.infra.exchange.base import ExchangeAdapter  # noqa: E402
from src.risk.kill_switch import clear_halt, is_halted  # noqa: E402

# ---------------------------------------------------------------------------
# Logging — suppress engine noise
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# ---------------------------------------------------------------------------
# Terminal colours
# ---------------------------------------------------------------------------
_IS_TTY = sys.stdout.isatty()


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _IS_TTY else s


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _IS_TTY else s


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if _IS_TTY else s


def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _IS_TTY else s


# ---------------------------------------------------------------------------
# Credential map (same logic as adapter_smoke_test.py)
# ---------------------------------------------------------------------------
_CRED_MAP: dict[str, tuple[str, str, str | None]] = {
    "binance":  ("BINANCE_API_KEY",   "BINANCE_API_SECRET",  None),
    "bybit":    ("BYBIT_API_KEY",     "BYBIT_API_SECRET",    None),
    "okx":      ("OKX_API_KEY",       "OKX_API_SECRET",      "OKX_PASSPHRASE"),
    "bitget":   ("BITGET_API_KEY",    "BITGET_API_SECRET",   "BITGET_PASSPHRASE"),
    "upbit":    ("UPBIT_API_KEY",     "UPBIT_API_SECRET",    None),
    "bithumb":  ("BITHUMB_API_KEY",   "BITHUMB_API_SECRET",  None),
    "coinone":  ("COINONE_API_KEY",   "COINONE_API_SECRET",  None),
}

_SYMBOL_MAP: dict[str, str] = {
    "binance": "BTC/USDT",
    "bybit":   "BTC/USDT",
    "okx":     "BTC/USDT",
    "bitget":  "BTC/USDT",
    "upbit":   "BTC/KRW",
    "bithumb": "BTC/KRW",
    "coinone": "BTC/KRW",
}

_MIN_QTY: dict[str, Decimal] = {
    "binance":  Decimal("0.001"),
    "bybit":    Decimal("0.001"),
    "okx":      Decimal("0.001"),
    "bitget":   Decimal("0.001"),
    "upbit":    Decimal("0.0001"),
    "bithumb":  Decimal("0.001"),
    "coinone":  Decimal("0.0001"),
}

# ---------------------------------------------------------------------------
# Mock adapter — leg2 always raises to force rollback
# ---------------------------------------------------------------------------


class _FailAdapter:
    """Minimal stub that always raises on place_order to simulate leg2 failure."""

    exchange_id: str = "mock_fail"

    @property
    def health_score(self) -> float:
        # Return value above AtomicExecutor threshold so health check passes
        return 1.0

    async def place_order(self, order: Order) -> Trade:
        raise RuntimeError("[SIMULATED] leg2 exchange failure")

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        return True

    async def cancel_all_orders(self, symbol: Any = None) -> int:
        return 0

    async def get_balances(self) -> dict:
        return {}

    async def get_positions(self) -> list:
        return []

    async def get_orderbook_snapshot(self, symbol: str, depth: int = 20) -> OrderBook:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Result file helpers
# ---------------------------------------------------------------------------
_RESULTS_PATH = _REPO_ROOT / ".omc" / "state" / "plumbing-results.json"

_RESULTS_SCHEMA: dict = {
    "session": "plumbing-v3-2026-04-05",
    "phase0": {},
    "phase1_5_rollback": {},
    "cases": {},
    "summary": {"total": 15, "pass": 0, "fail": 0, "skip": 0, "blocked": 0},
}


def _load_results() -> dict:
    if _RESULTS_PATH.exists():
        try:
            return json.loads(_RESULTS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_RESULTS_SCHEMA)


def _save_results(data: dict) -> None:
    _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Step printer
# ---------------------------------------------------------------------------

def _step(name: str, elapsed_ms: float, ok: bool, detail: str = "") -> None:
    label = f"[rollback_test] {name}"
    dots = "." * max(1, 38 - len(label))
    status = _green("PASS") if ok else _red("FAIL")
    timing = f"({elapsed_ms:.0f}ms)"
    detail_str = f"({detail})" if detail else ""
    print(f"{label} {dots} {status} {timing} {detail_str}".rstrip())


def _step_sim(name: str, detail: str = "") -> None:
    """Print a SIMULATED step (no PASS/FAIL)."""
    label = f"[rollback_test] {name}"
    dots = "." * max(1, 38 - len(label))
    detail_str = f"({detail})" if detail else ""
    print(f"{label} {dots} {_yellow('SIMULATED')} {detail_str}".rstrip())


# ---------------------------------------------------------------------------
# Core rollback test
# ---------------------------------------------------------------------------

async def run_rollback_test(exchange_id: str) -> dict:
    """
    Execute the rollback validation sequence and return a result dict.

    Steps:
      1. Connect to real exchange adapter
      2. Place an unfillable leg1 limit order (price × 0.50)
      3. Build a cross-exchange executor with a MockAdapter as exchange B
      4. Call execute_cross_exchange() — leg2 fails, triggering rollback
      5. Verify AtomicExecutor performed rollback (leg1 cancelled)
      6. Verify is_halted() == False (successful rollback should NOT set halt)
    """
    result: dict = {
        "exchange": exchange_id,
        "steps": {},
        "overall": "fail",
    }

    creds = _CRED_MAP.get(exchange_id)
    symbol = _SYMBOL_MAP.get(exchange_id, "BTC/USDT")
    min_qty = _MIN_QTY.get(exchange_id, Decimal("0.001"))

    if creds is None:
        print(_yellow(f"[rollback_test] SKIP — {exchange_id} not in credential map"))
        result["overall"] = "skip"
        result["reason"] = "no_cred_map"
        _persist(result)
        return result

    key_env, secret_env, pass_env = creds
    api_key = os.environ.get(key_env, "").strip()
    api_secret = os.environ.get(secret_env, "").strip()
    passphrase = os.environ.get(pass_env, "").strip() if pass_env else ""

    if not api_key or not api_secret:
        print(_yellow(
            f"[rollback_test] SKIP — {key_env} or {secret_env} not set"
        ))
        result["overall"] = "skip"
        result["reason"] = "missing_credentials"
        _persist(result)
        return result

    # ── Connect real adapter ─────────────────────────────────────────────────
    real_adapter = create_native_adapter(
        exchange_id=exchange_id,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
    )

    t0 = time.monotonic()
    try:
        await real_adapter.connect()
        elapsed = (time.monotonic() - t0) * 1000
        result["steps"]["connect"] = {"status": "pass", "ms": round(elapsed, 1)}
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        print(_red(f"[rollback_test] connect FAIL ({elapsed:.0f}ms): {exc}"))
        traceback.print_exc()
        result["steps"]["connect"] = {"status": "fail", "error": str(exc)}
        _persist(result)
        return result

    # ── Fetch current price ──────────────────────────────────────────────────
    try:
        ob = await real_adapter.get_orderbook_snapshot(symbol, depth=5)
        current_price = ob.best_ask or ob.best_bid
        if current_price is None:
            raise RuntimeError("Orderbook has no price levels")
        limit_price = (current_price * Decimal("0.50")).quantize(Decimal("0.01"))
    except Exception as exc:
        print(_red(f"[rollback_test] orderbook FAIL: {exc}"))
        traceback.print_exc()
        await _safe_disconnect(real_adapter)
        result["steps"]["orderbook"] = {"status": "fail", "error": str(exc)}
        _persist(result)
        return result

    # ── Build AtomicExecutor with real adapter + mock fail adapter ────────────
    mock_adapter = _FailAdapter()
    exchanges: dict[str, Any] = {
        exchange_id: real_adapter,
        "mock_fail": mock_adapter,
    }
    config = ExecutionConfig(
        timeout_ms=5000,
        partial_fill_threshold=Decimal("0.80"),
        post_reconcile_delay_s=0.1,  # minimal delay for test
        health_threshold=0.0,        # bypass health check for mock adapter
    )
    executor = AtomicExecutor(exchanges=exchanges, config=config)

    # Build leg1 (real unfillable limit) and leg2 (mock — will fail)
    leg1_order = Order(
        client_order_id=f"rb-leg1-{uuid.uuid4().hex[:8]}",
        exchange_id=exchange_id,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=limit_price,
        amount=min_qty,
    )
    leg2_order = Order(
        client_order_id=f"rb-leg2-{uuid.uuid4().hex[:8]}",
        exchange_id="mock_fail",
        symbol=symbol,
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        price=limit_price * Decimal("2"),
        amount=min_qty,
    )

    # ── STEP: leg1 place ─────────────────────────────────────────────────────
    # We place leg1 directly first to confirm it works and capture the order_id,
    # then use execute_cross_exchange() which will place it again internally.
    # To avoid double-placing, we instead run the executor end-to-end and inspect
    # the result — the executor places leg1, fails on leg2, rolls back leg1.
    _step_sim("leg2 fail", "MockAdapter.place_order raises RuntimeError")

    t0 = time.monotonic()
    try:
        exec_result = await executor.execute_cross_exchange(
            leg1_order=leg1_order,
            leg2_order=leg2_order,
            strategy_id="rollback_test",
            min_edge=Decimal("0"),
        )
        elapsed = (time.monotonic() - t0) * 1000
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _step("execute (unexpected exception)", elapsed, False, str(exc)[:80])
        traceback.print_exc()
        result["steps"]["execute"] = {"status": "fail", "error": str(exc)}
        await _safe_disconnect(real_adapter)
        _persist(result)
        return result

    # ── STEP: leg1 place ─────────────────────────────────────────────────────
    leg1_ok = exec_result.leg1 is not None and exec_result.leg1.trade is not None
    leg1_detail = ""
    if exec_result.leg1 and exec_result.leg1.trade:
        oid = exec_result.leg1.trade.order_id or exec_result.leg1.trade.trade_id
        leg1_detail = f"order_id: {oid}"
    _step("leg1 place", elapsed, leg1_ok, leg1_detail)
    result["steps"]["leg1_place"] = {
        "status": "pass" if leg1_ok else "fail",
        "ms": round(elapsed, 1),
        "detail": leg1_detail,
    }

    # ── STEP: rollback ───────────────────────────────────────────────────────
    # A successful rollback yields ROLLED_BACK status (not ROLLBACK_FAILED).
    rollback_ok = exec_result.status in (
        ExecutionStatus.ROLLED_BACK,
        # If leg1 was never placed (e.g. exchange rejected it outright), the
        # executor may return REJECTED — that is acceptable for the rollback path.
        ExecutionStatus.REJECTED,
    )
    rollback_detail = f"status={exec_result.status}"
    if exec_result.error:
        rollback_detail += f" error={exec_result.error[:60]}"
    _step("rollback", elapsed, rollback_ok, rollback_detail)
    result["steps"]["rollback"] = {
        "status": "pass" if rollback_ok else "fail",
        "execution_status": str(exec_result.status),
        "error": exec_result.error,
    }

    # ── STEP: halt check ─────────────────────────────────────────────────────
    halted = is_halted()
    halt_ok = not halted
    t1 = time.monotonic()
    elapsed_halt = (t1 - t0) * 1000

    if halted:
        # Rollback failed → halt was set. Auto-clear and warn.
        clear_halt()
        _step("halt check", elapsed_halt, False,
              "HALTED after rollback — cleared automatically, check positions")
        result["steps"]["halt_check"] = {
            "status": "fail",
            "detail": "halt was set — rollback_failed path triggered",
        }
        print(_yellow(
            "\n  HALT CLEARED — verify no open positions on the exchange manually.\n"
        ))
    else:
        _step("halt check", elapsed_halt, True, "not halted")
        result["steps"]["halt_check"] = {"status": "pass"}

    await _safe_disconnect(real_adapter)

    # ── OVERALL ──────────────────────────────────────────────────────────────
    all_pass = (
        leg1_ok
        and rollback_ok
        and halt_ok
    )
    result["overall"] = "pass" if all_pass else "fail"
    overall_str = _green("PASS") if all_pass else _red("FAIL")
    print(_bold(f"[rollback_test] OVERALL: {overall_str}"))

    _persist(result)
    return result


async def _safe_disconnect(adapter: Any) -> None:
    try:
        await adapter.disconnect()
    except Exception:
        pass


def _persist(result: dict) -> None:
    """Write result into phase1_5_rollback section of plumbing-results.json."""
    data = _load_results()
    exchange = result.get("exchange", "unknown")
    data.setdefault("phase1_5_rollback", {})[exchange] = result
    _save_results(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate AtomicExecutor rollback path (leg1 place → leg2 fail → rollback).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/rollback_test.py --exchange binance
  python scripts/rollback_test.py --exchange upbit
  python scripts/rollback_test.py --exchange bitget

What this tests:
  1. Places a real unfillable limit order as leg1 (price × 0.50)
  2. Simulates leg2 failure via MockAdapter
  3. Confirms AtomicExecutor rolls back (cancels) leg1
  4. Confirms halt flag is NOT set after a successful rollback
""",
    )
    parser.add_argument(
        "--exchange",
        required=True,
        help="Real exchange to use for leg1 (e.g. binance, upbit, bitget)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    exchange_id = args.exchange.lower().strip()
    print(_bold(f"\n=== Rollback Test: {exchange_id} ===\n"))
    result = asyncio.run(run_rollback_test(exchange_id))
    sys.exit(0 if result.get("overall") in ("pass", "skip") else 1)


if __name__ == "__main__":
    main()
