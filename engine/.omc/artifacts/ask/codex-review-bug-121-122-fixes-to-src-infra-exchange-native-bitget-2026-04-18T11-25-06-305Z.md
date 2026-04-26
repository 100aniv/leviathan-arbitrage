# codex advisor artifact

- Provider: codex
- Exit code: 0
- Created at: 2026-04-18T11:25:06.307Z

## Original task

Review BUG-121/122 fixes to src/infra/exchange/native_bitget.py (marginMode=isolated for futures WS) and native_binance.py (Trade model validation: non-zero price+qty fallback from order). Check: edge cases (MARKET orders, limit fills, Pydantic validation). 80 words.

## Final prompt

Review BUG-121/122 fixes to src/infra/exchange/native_bitget.py (marginMode=isolated for futures WS) and native_binance.py (Trade model validation: non-zero price+qty fallback from order). Check: edge cases (MARKET orders, limit fills, Pydantic validation). 80 words.

## Raw output

```text
1. High: `src/infra/exchange/native_bitget.py:84-97` hardcodes `marginMode="isolated"`. The adapter already detects/caches actual mode in `self._margin_mode` (`:147-154`, REST uses it at `:364`), so crossed-margin futures accounts will still fail WS orders. This should pass the detected/configured mode, not a constant.

2. High: `src/infra/exchange/native_binance.py:112-126` still returns `price=0` for MARKET orders when WS responds `avgPrice=0`, `price=0`, and `order.price is None`. Unlike REST (`:449-490`), there is no poll-before-build. Also, `Trade` has no non-zero Pydantic validator; the real constructor risk here is missing `exchange_id`.


OpenAI Codex v0.115.0 (research preview)
--------
workdir: /Users/100aniv/Development/arbitrage_OMC/engine
model: gpt-5.4
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: none
reasoning summaries: none
session id: 019da055-a5f3-7a01-b879-f78e5074395f
--------
user
Review BUG-121/122 fixes to src/infra/exchange/native_bitget.py (marginMode=isolated for futures WS) and native_binance.py (Trade model validation: non-zero price+qty fallback from order). Check: edge cases (MARKET orders, limit fills, Pydantic validation). 80 words.
mcp: chrome-devtools starting
mcp: chrome-devtools ready
mcp startup: ready: chrome-devtools
codex
Reviewing the BUG-121/122 fixes with a code-review lens. I’m checking the exact changes in `native_bitget.py` and `native_binance.py`, then I’ll validate edge cases around futures WS payloads, MARKET/LIMIT fills, and Pydantic model constraints.
exec
/bin/zsh -lc "nl -ba src/infra/exchange/native_bitget.py | sed -n '1,260p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
     1	"""Native Bitget adapter — Spot trading via direct REST + WebSocket (no ccxt)."""
     2	from __future__ import annotations
     3	
     4	import base64
     5	import hashlib
     6	import hmac
     7	import json
     8	import logging
     9	import math
    10	import time
    11	from decimal import Decimal
    12	from typing import Any
    13	
    14	from src.core.config_loader import get_config
    15	from src.core.models import Balance, FeeRate, Order, OrderBook, OrderSide, OrderType, Position, Trade
    16	from src.infra.exchange.native_adapter import NativeAdapter
    17	from src.infra.exchange.rate_limiter import RateLimitConfig
    18	
    19	logger = logging.getLogger(__name__)
    20	
exec
/bin/zsh -lc "nl -ba src/infra/exchange/native_binance.py | sed -n '1,320p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
     1	"""Binance native adapter — REST + WebSocket, no ccxt dependency.
     2	
     3	Auth: HMAC-SHA256 via timestamp + recvWindow query params.
     4	Spot:   https://api.binance.com  /  wss://stream.binance.com:9443
     5	Testnet: https://testnet.binance.vision
     6	"""
     7	from __future__ import annotations
     8	
     9	import asyncio
    10	import json
    11	import logging
    12	import zlib
    13	from datetime import datetime, timezone
    14	from decimal import Decimal
    15	from typing import Any
    16	
    17	# Binance order status constants
    18	_ORDER_STATUS_NEW = "NEW"
    19	_ORDER_STATUS_FILLED = "FILLED"
    20	
exec
/bin/zsh -lc 'git diff -- src/infra/exchange/native_bitget.py src/infra/exchange/native_binance.py' in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
diff --git a/engine/src/infra/exchange/native_binance.py b/engine/src/infra/exchange/native_binance.py
index 4a8e11b..f08beed 100644
--- a/engine/src/infra/exchange/native_binance.py
+++ b/engine/src/infra/exchange/native_binance.py
@@ -105,16 +105,25 @@ class BinanceNativeAdapter(NativeAdapter):
             quantity=order.amount,
             price=order.price if otype == "LIMIT" else None,
         )
-        # Parse WS response → Trade
+        # Parse WS response → Trade (BUG-122: Trade model requires non-zero price)
         if resp.get("status") != 200:
             raise RuntimeError(f"ws_place_order rejected: {resp}")
         result = resp.get("result", {})
+        _px_raw = result.get("avgPrice") or result.get("price") or order.price or Decimal("0")
+        _px = Decimal(str(_px_raw))
+        # MARKET orders return avgPrice=0 until fill confirmation; fallback to order.price
+        if _px <= 0 and order.price:
+            _px = order.price
+        _qty_raw = result.get("executedQty") or result.get("origQty") or order.amount
+        _qty = Decimal(str(_qty_raw))
codex
I have the two diffs. One more pass is needed against the `Trade` model and the WS client interfaces, because the risk here is not syntax but whether these fallbacks still violate model constraints or exchange-specific request rules under partial-fill/new-order responses.
exec
/bin/zsh -lc "rg -n \"def place_order\\(|marginMode|avgPrice|executedQty|origQty\" src/infra/exchange/ws_trade.py src/infra/exchange -g '"'!**/__pycache__/**'"'" in /Users/100aniv/Development/arbitrage_OMC/engine exited 2 in 0ms:
rg: src/infra/exchange/ws_trade.py: No such file or directory (os error 2)
src/infra/exchange/ccxt_adapter.py:181:    async def place_order(self, order: Order) -> Trade:
src/infra/exchange/native_bybit.py:215:        avg_price_str = result.get("avgPrice", "0") or "0"
src/infra/exchange/native_adapter.py:212:    async def place_order(self, order: Order) -> Trade:
src/infra/exchange/base.py:42:    async def place_order(self, order: Order) -> Trade:
src/infra/exchange/native_bitget.py:84:        # BUG-121: Bitget V2 futures WS requires marginMode (isolated/cross)
src/infra/exchange/native_bitget.py:87:            extra_params["marginMode"] = "isolated"
src/infra/exchange/native_bitget.py:147:                # BUG-107: also detect marginMode from same endpoint.
src/infra/exchange/native_bitget.py:152:                    raw_margin = data[0].get("marginMode", "crossed")
src/infra/exchange/native_bitget.py:362:                # BUG-107: marginMode detected at connect() via _fetch_pos_mode.
src/infra/exchange/native_bitget.py:364:                "marginMode": self._margin_mode,
src/infra/exchange/native_bitget.py:583:        # Poll /api/v2/mix/order/detail up to 3 times to get actual avgPrice + baseVolume.
src/infra/exchange/native_bitget.py:781:        avg_price = Decimal(str(rd.get("avgPrice", "0"))) if rd.get("avgPrice") else price
src/infra/exchange/ws_trade/bitget_ws_trade.py:132:    async def place_order(
src/infra/exchange/ws_trade/bitget_ws_trade.py:142:        marginMode: Optional[str] = None,
src/infra/exchange/ws_trade/bitget_ws_trade.py:159:        if marginMode:
src/infra/exchange/ws_trade/bitget_ws_trade.py:160:            params["marginMode"] = marginMode
src/infra/exchange/native_binance.py:112:        _px_raw = result.get("avgPrice") or result.get("price") or order.price or Decimal("0")
src/infra/exchange/native_binance.py:114:        # MARKET orders return avgPrice=0 until fill confirmation; fallback to order.price
src/infra/exchange/native_binance.py:117:        _qty_raw = result.get("executedQty") or result.get("origQty") or order.amount
exec
/bin/zsh -lc "rg -n \"class Trade|price:|amount:\" src/core/models.py src/core -g '"'!**/__pycache__/**'"'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/core/signal.py:248:        if sell_price <= buy_price:
src/core/triangular_scanner.py:173:        price: Decimal,
src/core/triangular_scanner.py:191:        price: Decimal,
src/core/triangular_scanner.py:298:        def rate(price: Decimal, side: str) -> Decimal:
src/core/events.py:45:class TradeEvent(BaseEvent):
src/core/models.py:37:    price: Decimal
src/core/models.py:38:    amount: Decimal
src/core/models.py:77:    price: Decimal | None = None
src/core/models.py:78:    amount: Decimal
src/core/models.py:87:class Trade(BaseModel):
src/core/models.py:93:    price: Decimal
src/core/models.py:94:    amount: Decimal
src/core/models.py:105:    entry_price: Decimal
src/core/models.py:106:    mark_price: Decimal | None = None
src/core/models.py:117:    buy_price: Decimal
src/core/models.py:118:    sell_price: Decimal
src/core/stale_detector.py:153:                mid_price=f"{mid_price:.6f}",
src/core/config.py:422:    btc_reference_price: Decimal = Field(default=Decimal("50000"), alias="BTC_REFERENCE_PRICE")
src/core/price_hub.py:14:    price: Decimal
src/core/price_hub.py:52:            if best is None or bid_price > best.price:
exec
/bin/zsh -lc "nl -ba src/infra/exchange/native_binance.py | sed -n '440,500p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/core/models.py | sed -n '80,120p'" in /Users/100aniv/Development/arbitrage_OMC/engineexec
/bin/zsh -lc "nl -ba src/infra/exchange/ws_trade/bitget_ws_trade.py | sed -n '120,185p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
    80	    remaining: Decimal | None = None
    81	    status: OrderStatus = OrderStatus.PENDING
    82	    created_at: datetime = Field(default_factory=_utcnow)
    83	    updated_at: datetime = Field(default_factory=_utcnow)
    84	    metadata: dict[str, Any] = Field(default_factory=dict)
    85	
    86	
    87	class Trade(BaseModel):
    88	    trade_id: str
    89	    order_id: str | None = None
    90	    exchange_id: str
    91	    symbol: str
    92	    side: OrderSide
    93	    price: Decimal
    94	    amount: Decimal
    95	    fee: Decimal = Decimal("0")
    96	    fee_currency: str | None = None
    97	    timestamp: datetime = Field(default_factory=_utcnow)
    98	    metadata: dict[str, Any] = Field(default_factory=dict)
    99	
 succeeded in 0ms:
   120	                    args = msg.get("arg") or []
   121	                    req_id = args[0].get("id") if args else None
   122	                    if req_id:
   123	                        fut = self._futures.pop(req_id, None)
   124	                        if fut and not fut.done():
   125	                            fut.set_result(msg)
   126	                except Exception as exc:
   127	                    logger.warning("BitgetWSTrade listen err: %s", exc)
   128	        except Exception as exc:
   129	            logger.warning("BitgetWSTrade listener closed: %s", exc)
   130	            self._running = False
   131	
   132	    async def place_order(
   133	        self,
   134	        inst_type: str,
   135	        inst_id: str,
   136	        order_type: str,
   137	        side: str,
   138	        size: Decimal,
   139	        price: Optional[Decimal] = None,
 succeeded in 0ms:
   440	                    order=order,
   441	                    trade_id=f"ghost-cleared-{order.order_id}",
   442	                    price=order.price or Decimal("0"),
   443	                    amount=order.amount,
   444	                )
   445	            raise
   446	
   447	        trade_id = str(raw.get("orderId", ""))
   448	
   449	        # PHOENIX: Binance Futures MARKET orders return status="NEW" executedQty="0" initially.
   450	        # Poll order status up to 2 times (100ms each) until status="FILLED".
   451	        # MARKET orders fill instantly on futures; polling is confirmation only.
   452	        if (
   453	            self._market_type == "futures"
   454	            and order.order_type == OrderType.MARKET
   455	            and raw.get("status") in (_ORDER_STATUS_NEW, None)
   456	            and trade_id
   457	        ):
   458	            for _attempt in range(2):
   459	                await asyncio.sleep(0.1)
exec
/bin/zsh -lc "nl -ba src/infra/exchange/ws_trade/binance_ws_trade.py | sed -n '90,145p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
    90	            self._running = False
    91	
    92	    def _sign(self, params_str: str) -> str:
    93	        return hmac.new(
    94	            self._api_secret.encode(), params_str.encode(), hashlib.sha256
    95	        ).hexdigest()
    96	
    97	    async def place_order(
    98	        self,
    99	        symbol: str,
   100	        side: str,
   101	        order_type: str,
   102	        quantity: Decimal,
   103	        price: Optional[Decimal] = None,
   104	        time_in_force: str = "GTC",
   105	    ) -> dict[str, Any]:
   106	        """Send order.place via WS. Returns parsed response dict.
   107	
   108	        Raises TimeoutError after _RESPONSE_TIMEOUT_S if no response.
   109	        """
exec
/bin/zsh -lc "rg -n \"field_validator|model_validator|gt=|ge=|validate.*Trade|Trade\\(\" src/core src/infra -g '"'!**/__pycache__/**'"'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/infra/db/schema.py:95:class Trade(Base):
src/infra/fx/krw_rate_provider.py:89:            logger.warning("KRWRateProvider.stale age=%.1fs — using fallback", age)
src/core/signal.py:301:                        "stale_orderbook_rejected symbol=%s exchange=%s age=%.1fs",
src/core/signal.py:311:                        "blacklisted_rejected symbol=%s exchange=%s", symbol, ob.exchange
src/core/signal.py:321:                    "low_update_count_rejected symbol=%s exchange=%s count=%d min=%d",
src/core/signal.py:343:                buy_exchange=buy_exchange,
src/core/signal.py:344:                sell_exchange=sell_exchange,
src/core/signal.py:596:            buy_exchange=buy_exchange,
src/core/signal.py:597:            sell_exchange=sell_exchange,
src/core/real_signal_producer.py:512:                        early_return_reason=f"reconnect_cooldown={_rc_ex} age={round(_now_rc - _rc_last, 1)}s",
src/core/real_signal_producer.py:651:                        buy_exchange=ex_b,
src/core/real_signal_producer.py:652:                        sell_exchange=ex_a,
src/core/real_signal_producer.py:726:                        buy_exchange=ex_a,
src/core/real_signal_producer.py:727:                        sell_exchange=ex_b,
src/core/real_signal_producer.py:879:                buy_exchange=exchange_id,
src/core/real_signal_producer.py:880:                sell_exchange=exchange_id,
src/core/real_signal_producer.py:956:                fast_exchange=fast_ex,
src/core/real_signal_producer.py:957:                slow_exchange=slow_ex,
src/core/real_signal_producer.py:1032:                    buy_exchange=usdt_exchange,
src/core/real_signal_producer.py:1033:                    sell_exchange=krw_exchange,
codex
The model shape adds another concern: `Trade` itself still requires `exchange_id`, so I’m checking whether these WS paths are already broken independently of the new fallback logic or whether another layer injects it. If the constructor is called directly, that affects how serious the reviewed fixes are.
exec
/bin/zsh -lc "nl -ba src/infra/exchange/native_adapter.py | sed -n '220,255p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   220	        except Exception:
   221	            pass
   222	        try:
   223	            trade: Optional[Trade] = None
   224	            if _use_ws and hasattr(self, "_ws_place_order"):
   225	                try:
   226	                    trade = await self._ws_place_order(order)
   227	                    _path = "ws"
   228	                except Exception as _ws_exc:
   229	                    logger.warning(
   230	                        "ws_place_order failed exchange=%s symbol=%s — falling back to REST: %s",
   231	                        self.exchange_id, order.symbol, _ws_exc,
   232	                    )
   233	                    trade = None
   234	            if trade is None:
   235	                trade = await self._rest_place_order(order)
   236	                _path = "rest"
   237	            latency_ms = (time.monotonic() - start) * 1000
   238	            self._health.record_api_latency(latency_ms)
   239	            self._health.record_order_fill(True)
exec
/bin/zsh -lc "rg -n \"exchange_id=.*Trade\\(|Trade\\(\" src/infra/exchange/native_bitget.py src/infra/exchange/native_binance.py src/infra/exchange/native_adapter.py" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
src/infra/exchange/native_binance.py:88:            self._ws_trade = BinanceWSTrade(self._api_key, self._api_secret)
src/infra/exchange/native_binance.py:121:        return Trade(
src/infra/exchange/native_adapter.py:496:        return Trade(
src/infra/exchange/native_bitget.py:68:            self._ws_trade = BitgetWSTrade(
src/infra/exchange/native_bitget.py:102:        return Trade(
exec
/bin/zsh -lc "nl -ba src/infra/exchange/native_adapter.py | sed -n '488,505p'" in /Users/100aniv/Development/arbitrage_OMC/engine succeeded in 0ms:
   488	        order: Order,
   489	        trade_id: str,
   490	        price: Decimal,
   491	        amount: Decimal,
   492	        fee: Decimal = Decimal("0"),
   493	        fee_currency: str | None = None,
   494	    ) -> Trade:
   495	        """Build Trade model from fill data."""
   496	        return Trade(
   497	            trade_id=trade_id,
   498	            order_id=order.order_id or order.client_order_id,
   499	            exchange_id=self.exchange_id,
   500	            symbol=order.symbol,
   501	            side=order.side,
   502	            price=price,
   503	            amount=amount,
   504	            fee=fee,
   505	            fee_currency=fee_currency,
codex
1. High: `src/infra/exchange/native_bitget.py:84-97` hardcodes `marginMode="isolated"`. The adapter already detects/caches actual mode in `self._margin_mode` (`:147-154`, REST uses it at `:364`), so crossed-margin futures accounts will still fail WS orders. This should pass the detected/configured mode, not a constant.

2. High: `src/infra/exchange/native_binance.py:112-126` still returns `price=0` for MARKET orders when WS responds `avgPrice=0`, `price=0`, and `order.price is None`. Unlike REST (`:449-490`), there is no poll-before-build. Also, `Trade` has no non-zero Pydantic validator; the real constructor risk here is missing `exchange_id`.
tokens used
29,418

```

## Concise summary

Provider completed successfully. Review the raw output for details.

## Action items

- Review the response and extract decisions you want to apply.
- Capture follow-up implementation tasks if needed.
