# LEVIATHAN 기관급 퀀트 재구축 실행 가이드

**Plan ID:** `leviathan-reconstruction-v1`
**작성일:** 2026-03-06
**상태:** Phase 0 구현 시작

---

## 현재 코드베이스 상태 (As-Is)

### 핵심 문제점
1. **ExchangeAdapter 프로토콜 비호환**: `base.py`의 `cancel_all_orders(symbol) -> int` vs `kill_switch.py`의 `cancel_all_orders(timeout_ms) -> list[str]`
2. **Rust PyO3 모듈 3개 고아 상태**: `rust_core` 컴파일됨 (OrderBook, Signal, KillSwitch) — 엔진 미연결
3. **합성 데이터만 검증**: GBM 기반 PaperExchangeAdapter로만 테스트
4. **ccxt Hot-path 레이턴시**: 모든 거래소 어댑터가 ccxt 기반
5. **BitgetAdapter __init__.py 미export**: import는 가능하나 `__all__`에 없음
6. **텔레그램/exa.ai 미활용**: 알림 시스템 없음

### 핵심 파일 맵

| 파일 | 위치 | 역할 | 줄수 |
|------|------|------|------|
| `base.py` | `engine/src/infra/exchange/base.py` | ExchangeAdapter Protocol (9메서드) | 70 |
| `kill_switch.py` | `engine/src/risk/kill_switch.py` | 3-Tier KillSwitch (별도 ExchangeAdapter Protocol) | 318 |
| `main.py` | `engine/src/main.py` | 10단계 Engine 초기화 | 623 |
| `signal.py` | `engine/src/core/signal.py` | SignalGenerator (friction-aware pipeline) | 191 |
| `order_book.py` | `engine/src/core/order_book.py` | L2 OrderBook (Decimal 기반) | 139 |
| `connection.py` | `engine/src/infra/db/connection.py` | asyncpg DatabasePool | 100 |
| `circuit_breaker.py` | `engine/src/risk/circuit_breaker.py` | CircuitBreaker (CLOSED→OPEN→HALF_OPEN) | 228 |
| `ccxt_adapter.py` | `engine/src/infra/exchange/ccxt_adapter.py` | 범용 CCXT 어댑터 | — |
| `bitget.py` | `engine/src/infra/exchange/bitget.py` | BitgetAdapter (CCXTAdapter 상속) | 50 |
| `__init__.py` | `engine/src/infra/exchange/__init__.py` | 어댑터 export (BitgetAdapter 누락!) | 30 |
| `pyproject.toml` | `engine/pyproject.toml` | 의존성 28개, httpx 없음(dev만) | 62 |
| `.env` | `.env` | 인프라+거래소+리스크 설정 | 92 |

### Rust PyO3 모듈 (engine/rust_core/)

| 모듈 | 클래스 | 메서드 |
|------|--------|--------|
| orderbook.rs | `PyOrderBook` | `apply_snapshot`, `apply_delta`, `best_bid`, `best_ask`, `spread`, `depth_weighted_mid_price` |
| signal.rs | `PySpreadCalculator`, `PyQuote` | `compute_spread_pct`, `best_bid_ask_across` |
| kill_switch.rs | `PyKillSwitch`, `PyKillSwitchEvent` | `halt_local`, `is_halted`, `clear_halt` |

---

## Phase 0: 기반 연결 (~8h)

### 0.1 ExchangeAdapter 프로토콜 통합
**파일**: `engine/src/infra/exchange/base.py`, `engine/src/risk/kill_switch.py`

**문제**: kill_switch.py 라인 62-74에 별도 `ExchangeAdapter` Protocol 정의
- `base.py`: `cancel_all_orders(symbol: str | None) -> int`
- `kill_switch.py`: `cancel_all_orders(timeout_ms: int) -> list[str]` + `close_all_positions(timeout_ms: int) -> list[str]`

**해결**:
1. `KillSwitchTarget` 프로토콜 생성 (kill_switch.py에서 분리)
2. `base.py`의 ExchangeAdapter에 `close_all_positions` 추가
3. `CCXTAdapter`가 양쪽 프로토콜 구현
4. `BitgetAdapter`를 `__init__.py`에 export

### 0.2 TimescaleDB 스키마
**생성**: `engine/src/infra/db/migrations/001_init_schema.sql`
**생성**: `engine/src/infra/db/market_recorder.py`

- `orderbook_snapshots` hypertable: ts, exchange, symbol, bids_json, asks_json, best_bid, best_ask, spread_bps
- `execution_log` hypertable: ts, strategy_id, buy_ex, sell_ex, symbol, buy_price, sell_price, size, net_pnl, status
- `ohlcv_1m` hypertable: ts, exchange, symbol, open, high, low, close, volume
- `MarketRecorder`: asyncpg 배치 인서트 (100ms flush, 1000행 버퍼)

### 0.3 텔레그램 알리미
**생성**: `engine/src/infra/telegram.py`

- `httpx` AsyncClient (fire-and-forget)
- Rate limit: 20msg/min
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ENABLED` 환경변수
- `send_alert()`, `send_kill_switch_event()`, `send_daily_summary()`
- KillSwitch.trigger() / CircuitBreaker.trip() → 텔레그램

### 0.4 Rust PyO3 브릿지
**생성**: `engine/src/core/rust_bridge.py`

- Per-module 환경변수: `USE_RUST_ORDERBOOK`, `USE_RUST_SIGNAL`, `USE_RUST_KILLSWITCH`
- 기본값: `false` (Python fallback)
- import 실패 시 graceful fallback + 경고 로그
- 미스컨피그 시 fail-loud

### 검증 기준
- [ ] docker compose up timescaledb → 테이블 존재 확인
- [ ] 텔레그램 테스트 메시지 수신
- [ ] USE_RUST_ORDERBOOK=false → 기존 동작 유지
- [ ] 기존 테스트 통과

---

## Phase 1: 실 데이터 수집 + 시그널 분석 (~10h)

### 1.1 Public WebSocket 수집기
**생성**: `engine/src/collectors/` 디렉토리

| 파일 | 거래소 | WebSocket URL |
|------|--------|---------------|
| `base_collector.py` | 추상 | — |
| `binance_collector.py` | Binance | `wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms` |
| `bybit_collector.py` | Bybit | `wss://stream.bybit.com/v5/public/spot` |
| `okx_collector.py` | OKX | `wss://ws.okx.com:8443/ws/v5/public` |
| `bitget_collector.py` | Bitget | `wss://ws.bitget.com/v2/ws/public` |
| `manager.py` | 오케스트레이션 | — |

- API 키 불필요 (public data only)
- `websockets` 직접 연결 (ccxt 불사용)
- 자동 재연결 (지수 백오프)
- 수집 → MarketRecorder → TimescaleDB

### 1.2 기존 파이프라인 연결
**수정**: `engine/src/main.py`

- `DataMode` enum: `SYNTHETIC`, `REAL_PUBLIC`, `REAL_AUTHENTICATED`
- `REAL_PUBLIC` 모드: 수집기 → `SignalGenerator.on_orderbook_update()` 직접 피드
- PaperExecutor로 관찰 모드
- Prometheus: `leviathan_signal_count`, `leviathan_spread_bps_histogram`

### 1.3 Walk-Forward 시그널 분석
**생성**: `engine/src/analysis/signal_analyzer.py`, `engine/src/analysis/walk_forward.py`

- 실 오더북 데이터 → SignalGenerator → 분포 분석
- Walk-Forward: 1h 롤링, Sharpe/MDD/승률
- **Sharpe < 2.5 → 라이브 차단**

---

## Phase 1.5: 수치 동등성 검증 (~4h)

**생성**: `engine/tests/numerical/test_rust_python_parity.py`

- Phase 1 실 데이터로 오프라인 비교
- Python Decimal vs Rust f64 — OrderBook 연산, 시그널 연산
- **허용치**: 가격 $0.0001-$200,000에서 최대 0.1 bps 발산

---

## Phase 2: Rust Hot-Path 연결 (~8h)

### 2.1 OrderBook 연결
- `USE_RUST_ORDERBOOK=true` → `rust_core.OrderBook` 사용
- 벤치마크: 10K 업데이트 P50/P99, Rust < 5μs

### 2.2 SpreadCalculator 시그널
- `USE_RUST_SIGNAL=true` → Rust `process()`
- Python friction filter 유지 (Decimal 정밀도)

### 2.3 Kill Switch 연결
- `USE_RUST_KILLSWITCH=true` → Python threading.Event + Rust AtomicBool OR 로직
- Rust는 Tier 1만. Tier 2/3 Python 유지

### 2.4 Observability 브릿지
- Rust → elapsed time → PyO3 콜백 → Prometheus 히스토그램

---

## Phase 3: Shadow Mode (~10h)

- Shadow Mode 오케스트레이터 (`engine/src/modes/shadow.py`)
- Sharpe Gate + Live 차단 (`engine/src/modes/live_gate.py`)
- Blueprint 준수 감사 (`engine/src/infra/compliance.py`)
- 7일 연속 운영, 텔레그램 일일 리포트

---

## Phase 4: Native Exchange Adapters (~45h)

- `native_base.py` → websockets + httpx 직접
- 거래소별: binance → bybit → okx → bitget → upbit → bithumb
- Shadow 72h 병렬 검증 후 전환
- ccxt 의존성 완전 제거

---

## Phase 5: Live Readiness (~8h)

- 10항목 자동 점검 스크립트
- 운영 문서 5개

---

## 의존성 그래프

```
Phase 0 (기반)
  └→ Phase 1 (실 데이터)
      └→ Phase 1.5 (수치 검증)
          └→ Phase 2 (Rust)
              └→ Phase 3 (Shadow)
                  └→ Phase 4 (ccxt 제거)
                      └→ Phase 5 (Live Ready)
```

**총 예상: ~93h | 첫 실 시그널: ~18h | Live 판단: Phase 3 후**
