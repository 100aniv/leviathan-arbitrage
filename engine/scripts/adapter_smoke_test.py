"""Adapter smoke test — verify live plumbing for a single exchange.

Validates four steps in sequence:
  1. connect()              — HTTP client init
  2. get_balances()         — authenticated REST
  3. place_limit_order()    — unfillable limit (price × 0.50)
  4. cancel_order()         — immediate cancel

Usage::

    cd engine
    python scripts/adapter_smoke_test.py --exchange binance
    python scripts/adapter_smoke_test.py --exchange upbit
    python scripts/adapter_smoke_test.py --exchange bithumb
    python scripts/adapter_smoke_test.py --exchange coinone
    python scripts/adapter_smoke_test.py --exchange bitget
    python scripts/adapter_smoke_test.py --exchange binance_futures

Results are appended to .omc/state/plumbing-results.json (phase0 section).
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

# ---------------------------------------------------------------------------
# Path setup — allow running as  `python scripts/adapter_smoke_test.py`
# from the engine/ root without installing the package.
# ---------------------------------------------------------------------------
_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

# Load .env from repo root (one level above engine/) before any src imports.
_REPO_ROOT = _ENGINE_ROOT.parent
_ENV_FILE = _REPO_ROOT / ".env"


def _load_env(path: Path) -> None:
    """Parse KEY=VALUE lines from a .env file into os.environ (no override)."""
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(str(path), override=False)
        return
    except ImportError:
        pass
    # Fallback: manual parse
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
# Now safe to import engine modules
# ---------------------------------------------------------------------------
from src.core.models import Order, OrderSide, OrderType  # noqa: E402
from src.infra.exchange import create_native_adapter  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,  # suppress engine noise; we print our own output
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# ---------------------------------------------------------------------------
# Terminal colours (graceful fallback if not a tty)
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
# Exchange credential map
# ---------------------------------------------------------------------------
# Maps exchange_id → (api_key_env, api_secret_env, passphrase_env | None)
# Note: BITGET_API_KEY has a trailing space in some .env files — strip() handles it.
_CRED_MAP: dict[str, tuple[str, str, str | None]] = {
    "binance":  ("BINANCE_API_KEY",   "BINANCE_API_SECRET",  None),
    "bybit":    ("BYBIT_API_KEY",     "BYBIT_API_SECRET",    None),
    "okx":      ("OKX_API_KEY",       "OKX_API_SECRET",      "OKX_PASSPHRASE"),
    "bitget":   ("BITGET_API_KEY",    "BITGET_API_SECRET",   "BITGET_PASSPHRASE"),
    "upbit":    ("UPBIT_API_KEY",     "UPBIT_API_SECRET",    None),
    "bithumb":  ("BITHUMB_API_KEY",   "BITHUMB_API_SECRET",  None),
    "coinone":  ("COINONE_ACCESS_TOKEN", "COINONE_API_SECRET",  None),
    "mexc":     ("MEXC_API_KEY",      "MEXC_API_SECRET",     None),
    "gateio":   ("GATEIO_API_KEY",    "GATEIO_API_SECRET",   None),
    "bingx":    ("BINGX_API_KEY",     "BINGX_API_SECRET",    None),
    "lbank":    ("LBANK_API_KEY",     "LBANK_API_SECRET",    None),
    "orangex":  ("ORANGEX_API_KEY",   "ORANGEX_API_SECRET",  None),
}

# Test symbol per exchange (base exchange, not futures variant)
_SYMBOL_MAP: dict[str, str] = {
    "binance":  "BTC/USDT",
    "bybit":    "BTC/USDT",
    "okx":      "BTC/USDT",
    "bitget":   "BTC/USDT",
    "upbit":    "BTC/KRW",
    "bithumb":  "BTC/KRW",
    "coinone":  "BTC/KRW",
    "mexc":     "BTC/USDT",
    "gateio":   "BTC/USDT",
    "bingx":    "BTC/USDT",
    "lbank":    "BTC/USDT",
    "orangex":  "BTC/USDT",
}

# Minimum order sizes to avoid exchange rejections (conservative)
_MIN_QTY: dict[str, Decimal] = {
    "binance":  Decimal("0.001"),  # spot: $5 min notional
    "bybit":    Decimal("0.001"),
    "okx":      Decimal("0.001"),
    "bitget":   Decimal("0.001"),
    "upbit":    Decimal("0.0001"),
    "bithumb":  Decimal("0.001"),
    "coinone":  Decimal("0.0001"),
    "mexc":     Decimal("0.001"),
    "gateio":   Decimal("0.001"),
    "bingx":    Decimal("0.001"),
    "lbank":    Decimal("0.001"),
    "orangex":  Decimal("0.001"),
}

# Futures min qty: Binance perp requires min $100 notional
# 0.004 BTC × $33,400 (50% of ~$66,800) = $133.6 > $100 ✅
_MIN_QTY_FUTURES: dict[str, Decimal] = {
    "binance": Decimal("0.004"),
}

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
# Step result helpers
# ---------------------------------------------------------------------------

def _step(exchange_id: str, name: str, elapsed_ms: float, ok: bool, detail: str = "") -> None:
    label = f"[{exchange_id}] {name}"
    dots = "." * max(1, 32 - len(label))
    status = _green("PASS") if ok else _red("FAIL")
    timing = f"({elapsed_ms:.0f}ms)"
    detail_str = f"({detail})" if detail else ""
    print(f"{label} {dots} {status} {timing} {detail_str}".rstrip())


# ---------------------------------------------------------------------------
# Core smoke test
# ---------------------------------------------------------------------------

async def run_smoke(exchange_id: str) -> dict:
    """Run all four smoke steps. Returns a result dict for plumbing-results.json."""
    # Resolve base exchange id for futures variants
    base_id = exchange_id.removesuffix("_futures")
    creds = _CRED_MAP.get(base_id)
    symbol = _SYMBOL_MAP.get(base_id, "BTC/USDT")
    is_futures = exchange_id.endswith("_futures")
    min_qty = (
        _MIN_QTY_FUTURES.get(base_id, _MIN_QTY.get(base_id, Decimal("0.001")))
        if is_futures
        else _MIN_QTY.get(base_id, Decimal("0.001"))
    )

    if creds is None:
        print(_yellow(f"[{exchange_id}] SKIP — no credential mapping defined"))
        return {"status": "skip", "reason": "no_cred_map"}

    key_env, secret_env, pass_env = creds
    api_key = os.environ.get(key_env, "").strip()
    api_secret = os.environ.get(secret_env, "").strip()
    passphrase = os.environ.get(pass_env, "").strip() if pass_env else ""

    if not api_key or not api_secret:
        print(_yellow(
            f"[{exchange_id}] SKIP — {key_env} or {secret_env} not set in .env"
        ))
        return {"status": "skip", "reason": "missing_credentials"}

    # Build result dict
    result: dict = {
        "exchange": exchange_id,
        "symbol": symbol,
        "steps": {},
        "overall": "fail",
    }

    adapter = create_native_adapter(
        exchange_id=exchange_id,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
    )

    # ── STEP 1: connect ──────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        await adapter.connect()
        elapsed = (time.monotonic() - t0) * 1000
        _step(exchange_id, "connect", elapsed, True)
        result["steps"]["connect"] = {"status": "pass", "ms": round(elapsed, 1)}
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _step(exchange_id, "connect", elapsed, False, str(exc)[:80])
        traceback.print_exc()
        result["steps"]["connect"] = {"status": "fail", "error": str(exc)}
        result["overall"] = "fail"
        _save_step(result)
        return result

    # ── STEP 2: get_balances ─────────────────────────────────────────────────
    balance_detail = ""
    t0 = time.monotonic()
    try:
        balances = await adapter.get_balances()
        elapsed = (time.monotonic() - t0) * 1000
        # Pick a representative balance to display
        if balances:
            key = next(iter(balances))
            b = balances[key]
            balance_detail = f"{key}: {float(b.free):.2f}"
        else:
            balance_detail = "empty"
        _step(exchange_id, "get_balances", elapsed, True, balance_detail)
        result["steps"]["get_balances"] = {
            "status": "pass",
            "ms": round(elapsed, 1),
            "detail": balance_detail,
        }
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        body = _http_error_body(exc)
        if body:
            print(_yellow(f"  response body: {body}"))
        _step(exchange_id, "get_balances", elapsed, False, str(exc)[:80])
        result["steps"]["get_balances"] = {"status": "fail", "error": str(exc), "body": body}
        await _safe_disconnect(adapter)
        _save_step(result)
        return result

    # ── STEP 3: place unfillable limit order ─────────────────────────────────
    order_id: str | None = None
    t0 = time.monotonic()
    try:
        ob = await adapter.get_orderbook_snapshot(symbol, depth=5)
        current_price = ob.best_ask or ob.best_bid
        if current_price is None:
            raise RuntimeError("Orderbook returned no price levels")

        quote_cur = (symbol.split("/")[1] if "/" in symbol else "USDT").upper()

        # Binance enforces PERCENT_PRICE_BY_SIDE: BUY limit price must be >= 80% of VWAP.
        # Use 85% for Binance, 50% for all other exchanges (wide margin = safely unfillable).
        if base_id == "binance":
            limit_pct = Decimal("0.85")
        else:
            limit_pct = Decimal("0.50")

        # KRW: round down to nearest 1000 (호가단위) using integer arithmetic to avoid
        # scientific notation (Decimal("1E+3").quantize → "5.2352E+7" breaks JWT hash)
        if exchange_id.endswith("_futures"):
            limit_price = (current_price * limit_pct).quantize(Decimal("0.1"))
        elif quote_cur == "KRW":
            limit_price = Decimal(int(current_price * limit_pct) // 1000 * 1000)
        else:
            limit_price = (current_price * limit_pct).quantize(Decimal("0.01"))

        # For spot: dynamically compute qty from available balance (not fixed min_qty).
        # This handles accounts with small balances — avoids insufficient-funds rejections.
        if not is_futures:
            quote = symbol.split("/")[1] if "/" in symbol else "USDT"
            avail = Decimal("0")
            for k, v in balances.items():
                if k.upper() == quote.upper():
                    avail = v.free
                    break
            if avail > Decimal("0") and limit_price > Decimal("0"):
                # Use 80% of available, quantized to 4 decimal places (safe for all spot)
                dyn_qty = (avail * Decimal("0.8") / limit_price).quantize(Decimal("0.0001"))
                if dyn_qty > Decimal("0"):
                    min_qty = dyn_qty

            # Guard: if balance can't cover even the minimum order, skip order placement.
            required = min_qty * limit_price
            if avail < required:
                detail = f"insufficient balance: {float(avail):.2f} {quote} < {float(required):.2f} {quote} needed"
                print(_yellow(f"[{exchange_id}] place_order BLOCKED — {detail}"))
                result["steps"]["place_order"] = {"status": "blocked", "detail": detail}
                result["overall"] = "blocked"
                await _safe_disconnect(adapter)
                _save_step(result)
                return result

        test_order = Order(
            client_order_id=f"smoke-{uuid.uuid4().hex[:8]}",
            exchange_id=base_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=limit_price,
            amount=min_qty,
        )

        trade = await adapter.place_order(test_order)
        # Use exchange's numeric order_id (trade_id) for cancellation, not client_order_id
        order_id = trade.trade_id or trade.order_id
        elapsed = (time.monotonic() - t0) * 1000
        _step(exchange_id, "place_order", elapsed, True, f"order_id: {order_id}")
        result["steps"]["place_order"] = {
            "status": "pass",
            "ms": round(elapsed, 1),
            "order_id": order_id,
            "price": str(limit_price),
        }
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        body = _http_error_body(exc)
        if body:
            print(_yellow(f"  response body: {body}"))
        _step(exchange_id, "place_order", elapsed, False, str(exc)[:80])
        result["steps"]["place_order"] = {"status": "fail", "error": str(exc), "body": body}
        await _safe_disconnect(adapter)
        _save_step(result)
        return result

    # ── STEP 4: cancel order ─────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        if order_id is None:
            raise RuntimeError("No order_id from previous step")
        cancelled = await adapter.cancel_order(order_id, symbol=symbol)
        elapsed = (time.monotonic() - t0) * 1000
        if cancelled:
            _step(exchange_id, "cancel_order", elapsed, True)
            result["steps"]["cancel_order"] = {"status": "pass", "ms": round(elapsed, 1)}
        else:
            # Some adapters return False for already-cancelled orders — treat as warn
            _step(exchange_id, "cancel_order", elapsed, False, "returned False")
            result["steps"]["cancel_order"] = {
                "status": "fail",
                "error": "cancel_order returned False",
            }
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _step(exchange_id, "cancel_order", elapsed, False, str(exc)[:80])
        traceback.print_exc()
        result["steps"]["cancel_order"] = {"status": "fail", "error": str(exc)}

    await _safe_disconnect(adapter)

    # ── OVERALL ──────────────────────────────────────────────────────────────
    all_pass = all(
        result["steps"].get(s, {}).get("status") == "pass"
        for s in ("connect", "get_balances", "place_order", "cancel_order")
    )
    result["overall"] = "pass" if all_pass else "fail"
    overall_str = _green("PASS") if all_pass else _red("FAIL")
    print(_bold(f"[{exchange_id}] OVERALL: {overall_str}"))

    _save_step(result)
    return result


def _http_error_body(exc: Exception) -> str:
    """Extract response body from httpx.HTTPStatusError if available."""
    try:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.text[:300]
    except Exception:
        pass
    return ""


async def _safe_disconnect(adapter) -> None:
    try:
        await adapter.disconnect()
    except Exception:
        pass


def _save_step(result: dict) -> None:
    """Persist result into phase0 section of plumbing-results.json."""
    data = _load_results()
    exchange = result.get("exchange", "unknown")
    data.setdefault("phase0", {})[exchange] = result
    # Recount summary
    phase0 = data.get("phase0", {})
    passes = sum(1 for v in phase0.values() if isinstance(v, dict) and v.get("overall") == "pass")
    fails = sum(1 for v in phase0.values() if isinstance(v, dict) and v.get("overall") == "fail")
    skips = sum(1 for v in phase0.values() if isinstance(v, dict) and v.get("status") == "skip")
    data.setdefault("summary", {}).update({"pass": passes, "fail": fails, "skip": skips})
    _save_results(data)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_ALL_EXCHANGES = [
    "binance", "binance_futures",
    "upbit", "bithumb", "coinone",
    "bitget", "bitget_futures",
]

_DISPLAY_NAME = {
    "binance": "Binance",
    "binance_futures": "Binance Futures",
    "upbit": "Upbit",
    "bithumb": "Bithumb",
    "coinone": "Coinone",
    "bitget": "Bitget",
    "bitget_futures": "Bitget Futures",
}


def _print_summary_table(results: list[dict]) -> None:
    """Print a rich summary table of all smoke test results."""
    rows = []
    for r in results:
        ex = r.get("exchange", "?")
        name = _DISPLAY_NAME.get(ex, ex)
        steps = r.get("steps", {})

        def cell(step: str) -> str:
            s = steps.get(step, {})
            st = s.get("status", "—")
            if st == "pass":
                detail = s.get("detail", "")
                if detail and step == "get_balances":
                    return f"✅ {detail}"
                return "✅"
            if st == "blocked":
                return "BLOCKED"
            if st == "skip":
                return "SKIP"
            if st == "fail":
                return "❌"
            return "—"

        overall = r.get("overall", "fail")
        verdict = "✅ PASS" if overall in ("pass", "blocked", "skip") else "❌ FAIL"
        rows.append((name, cell("connect"), cell("get_balances"), cell("place_order"), cell("cancel_order"), verdict))

    col_w = [max(len(r[i]) for r in rows + [("거래소", "connect", "get_balances", "place_order", "cancel", "판정")]) + 2 for i in range(6)]
    col_w[0] = max(col_w[0], 17)
    sep = "├" + "┼".join("─" * w for w in col_w) + "┤"
    top = "┌" + "┬".join("─" * w for w in col_w) + "┐"
    bot = "└" + "┴".join("─" * w for w in col_w) + "┘"
    hdr = ("거래소", "connect", "get_balances", "place_order", "cancel", "판정")

    def fmt_row(cells):
        return "│" + "│".join(f" {c:<{col_w[i]-2}} " for i, c in enumerate(cells)) + "│"

    print()
    print(top)
    print(fmt_row(hdr))
    for row in rows:
        print(sep)
        print(fmt_row(row))
    print(bot)
    print()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test exchange adapters (connect → balance → order → cancel).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/adapter_smoke_test.py --exchange binance
  python scripts/adapter_smoke_test.py --all
""",
    )
    parser.add_argument("--exchange", help="Exchange ID (e.g. binance, upbit, binance_futures)")
    parser.add_argument("--all", action="store_true", help="Run all configured exchanges and print summary table")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.all:
        async def run_all():
            return await asyncio.gather(*[run_smoke(ex) for ex in _ALL_EXCHANGES])
        results = asyncio.run(run_all())
        _print_summary_table(list(results))
        any_fail = any(r.get("overall") not in ("pass", "skip", "blocked") for r in results)
        sys.exit(1 if any_fail else 0)

    if not args.exchange:
        print("error: --exchange or --all required", file=sys.stderr)
        sys.exit(2)

    exchange_id = args.exchange.lower().strip()
    print(_bold(f"\n=== Adapter Smoke Test: {exchange_id} ===\n"))
    result = asyncio.run(run_smoke(exchange_id))
    sys.exit(0 if result.get("overall") in ("pass", "skip", "blocked") else 1)


if __name__ == "__main__":
    main()
