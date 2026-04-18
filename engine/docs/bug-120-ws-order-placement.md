# BUG-120: WebSocket Order Placement (REST → WS Migration)

**Status:** Design approved, implementation pending  
**Expected latency reduction:** 70-75%

## Problem

Current v163 order placement via REST:
- Binance Futures: 350-400ms per order
- Bitget Futures: 977-1057ms per order
- Total 2-leg trade: 2000-2670ms

## Solution — WebSocket Trading API

### Binance Futures WS (ws-fapi)
```
Endpoint: wss://ws-fapi.binance.com/ws-fapi/v1
Method:   order.place
Auth:     HMAC-SHA256 signature (API key + secret)
```

**Request:**
```json
{
  "id": "uuid",
  "method": "order.place",
  "params": {
    "apiKey": "${BINANCE_API_KEY}",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "MARKET",
    "quantity": "0.001",
    "timestamp": 1234567890123,
    "signature": "hmac_sha256_hex"
  }
}
```

### Bitget V2 WS Trade Channel
```
Endpoint: wss://ws.bitget.com/v2/ws/private
Login:    op=login, apiKey+passphrase+timestamp+signature
Order:    op=trade, channel=place-order
```

**Login + Order:**
```json
// Login (once per connection)
{"op":"login","args":[{"apiKey":"...","passphrase":"...","timestamp":"1234567890","sign":"base64_hmac"}]}

// Place order
{"op":"trade","args":[{"id":"uuid","instType":"USDT-FUTURES","instId":"BTCUSDT","channel":"place-order","params":{...}}]}
```

**Note:** Bitget "contact BD/RM to apply" — regular accounts may need market-maker approval.

## Implementation Plan

### Phase 1: Connection management
- `NativeAdapter._ws_trade_conn` property
- `_ws_trade_connect()` — connect + auth on first order
- `_ws_response_futures: dict[id, Future]` for correlation

### Phase 2: Order send/receive
- `_ws_place_order(order: Order) -> Trade`
- Build signed request, send JSON
- Await response Future (timeout 5s)
- Parse response → Trade object

### Phase 3: Wiring
- `place_order()` tries WS first, fallback to REST on exception
- Feature flag: `engine.json execution.ws_order_enabled=true`

### Phase 4: Testing
- Unit tests with fake WS server
- Integration test on Binance testnet
- Gradual rollout: shadow logging → flag toggle → production

## Risks

1. **Bitget access** — market-maker tier may be required
2. **Message ordering** — WS not guaranteed order (use id correlation)
3. **Connection drops** — reconnect + resubscribe logic needed
4. **Response timeout** — 5s hard limit, REST fallback

## Expected Results

| Exchange | REST (current) | WS (target) | Reduction |
|----------|---------------|-------------|-----------|
| Binance Futures | 350ms | 100ms | -70% |
| Bitget Futures | 1000ms | 200-300ms | -75% |
| **2-leg total** | **2000ms** | **~600ms** | **-70%** |

## References
- Binance WS API: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-api
- Bitget V2 Trade Channel: https://www.bitget.com/api-doc/spot/websocket/private/Place-Order-Channel
- Bitget latency upgrade 2026-04-15: 40% cut for PRO users
