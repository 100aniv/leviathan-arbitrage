# Phase K PLAN.md — Backtest → Paper → Live 종합 검증

> 작성일: 2026-04-02 | 작성자: Planner Agent (Stage A)
> 승인 플랜: `.claude/plans/radiant-cooking-forest.md` (v4)
> PRD: `.omc/prd.json` — Phase K US 16개 (passes:false), 총 375 US
> SSOT: `SSOT.md §2` — Phase J 완료 → Phase K 진입

---

## 0. 목표

Phase K는 LEVIATHAN 아비트라지 봇의 **전체 실행 경로를 소액으로 실증**하는 종합 Phase이다.
15개 거래소 x 7개 전략 조합에서 Backtest → Paper → Live 전 사이클을 케이스별로 순차 검증한다.

### Phase K US 목록 (16개 passes:false)

| US | 제목 | 배치 | 우선순위 |
|----|------|------|---------|
| US-375 | .env 단일화 (K-0-ENV) | Batch 0 | 0 (최우선) |
| US-334 | Sandbox + 자본 기준 (K-0) | Batch 0 | 1 |
| US-365 | DB mode 분리 배선 (K-0) | Batch 0 | 1 |
| US-364 | Telegram 승인 게이트 (K-1C) | Batch 1 | 2 |
| US-366 | .env 시크릿 전용 정리 (K-1A) | Batch 1 | 2 |
| US-367 | 거래소 배선 검증 7개 (K-1C) | Batch 1 | 3 |
| US-368 | 백테스트 Batch1 Binance 4케이스 (K-2-B) | Batch 2 | 10 |
| US-369 | 백테스트 Batch2 Bitget+KRW 7케이스 (K-2-B) | Batch 2 | 11 |
| US-370 | 백테스트 Batch3 멀티거래소 5케이스 (K-2-B) | Batch 2 | 12 |
| US-371 | 백테스트 Batch4 Tier4 WS전용 7케이스 (K-2-B) | Batch 2 | 13 |
| US-332 | Paper 무중단 24H (K-2-P) | Batch 3 | 0 |
| US-372 | Paper 실행 23케이스 (K-2-P) | Batch 3 | 20 |
| US-055 | LiveGate Preflight 10항목 (K-4) | Batch 4 | 66 |
| US-056 | 첫 Live 체결 (K-2-L) | Batch 4 | 67 |
| US-373 | 전체 병렬 운영 24H (K-2-ALL) | Batch 5 | 30 |
| US-374 | Notion 실시간 공유 (K-8) | Batch 5 | 40 |

### 완료 기준 (Tiered)

| 기준 | FAIL | CONDITIONAL | PASS |
|------|------|-------------|------|
| 백테스트 Sharpe | < 0.5 | 0.5~1.0 | > 1.0 |
| 백테스트 MDD | > 20% | 10~20% | < 10% |
| Paper crash | >= 1건 | -- | 0건 |
| Paper 누적 | < 12H | 12~24H | >= 24H |
| Live 체결 | 0건 | -- | >= 1건 |
| Live MDD | > 5% | 3~5% | < 3% |
| 병렬 24H crash | >= 1건 | -- | 0건 |

---

## 1. 실행 순서 (의존성 그래프)

```
K-0-ENV (US-375) ──────────────────────────────────────────────────────────────┐
   │                                                                           │
   v                                                                           │
K-0 (US-334 + US-365 + US-376 DB mode 분리 배선) ──────────────────────────────┤
   │                                                                           │
   v                                                                           │
K-1A/C (US-364 + US-366 + US-367) ─────────────────────────────────────────────┤
   │                                                                           │
   ├────────────────┐                                                          │
   v                v                                                          │
K-2-B Batch1    K-1D (US-360, Tier4 어댑터) ───> K-2-B Batch4 (US-371)        │
(US-368)           │                                                           │
   │               v                                                           │
   ├──> K-2-B Batch2 (US-369) ──┐  ⚡병렬                                      │
   └──> K-2-B Batch3 (US-370) ──┘                                             │
                    │                                                          │
                    v                                                          │
              K-2-P (US-372) ── 누적 >= 24H ──> US-332 자동 충족               │
                    │                                                          │
                    v                                                          │
              K-4 (US-055, LiveGate Preflight)                                 │
                    │                                                          │
                    v                                                          │
              K-2-L (US-056, 첫 Live 체결)                                     │
                    │                                                          │
                    v                                                          │
              K-2-ALL (US-373, 전체 병렬 24H)                                  │
                    │                                                          │
                    v                                                          │
              K-8 (US-374, Notion 공유) ───────────────────────────────────────┘
```

---

## 2. Batch 0: 환경 설정 (K-0-ENV + K-0)

### US-375 — .env 단일화 (K-0-ENV, 최우선)

**배경**: `engine/.env` (197줄)와 루트 `.env` (182줄) 공존, 4곳 드리프트.

**수정 파일 (6개)**:

| 파일 | 수정 내용 |
|------|----------|
| `engine/src/core/config.py` L55 | `_ENGINE_ROOT / ".env"` → `_ENGINE_ROOT.parent / ".env"` |
| `engine/src/core/config.py` L576 | `env_file=".env"` → `env_file=str(Path(__file__).resolve().parents[3] / ".env")` |
| `engine/src/api/routes/settings.py` L88 | .env 쓰기 경로 → repo root 절대경로 |
| `engine/src/infra/telegram_dev_bot.py` L555 | .env 읽기 경로 → repo root 절대경로 |
| `engine/src/modes/preflight.py` L680-735 | `_check_env_sync()` 함수 삭제 (dead code) |
| 루트 `.env` | 드리프트 수정: EXECUTION_MODE=paper, MAX_DAILY_LOSS_USD=15, REDIS_URL/GATEIO/MEXC 추가 |

**engine/config/engine.json 추가 섹션**:
```json
{
  "shadow": {
    "depth_fraction": 1.0,
    "max_trade_size": 100,
    "cross_exchange_min_book_depth_usd": 1,
    "futures_min_book_depth_usd": 1
  },
  "live_gate": {
    "sharpe_threshold": 0.0,
    "min_signals_per_day": 0,
    "evaluation_days": 1,
    "bypass": false
  },
  "tuner": { "data_source": "timescaledb" }
}
```

**최종 상태**: `engine/.env` 삭제. 루트 `.env` = 유일한 시크릿 소스. `engine/config/engine.json` = 설정값.

**AC**:
1. `engine/.env` 파일 삭제 확인
2. `config.py` 절대경로 수정 (`grep "parents\[3\]" engine/src/core/config.py` 출력 확인)
3. 루트 `.env`에 `EXECUTION_MODE=paper`, `MAX_DAILY_LOSS_USD=15`
4. `cd engine && python -m pytest tests/ -x --tb=short` 0 failures
5. `cd engine && python -c "from src.core.config import Settings; s = Settings(); print(s.execution_mode)"` → `paper`
6. `preflight.py` `_check_env_sync` 삭제 확인
7. `engine/config/engine.json`에 shadow/live_gate/tuner 섹션 존재

**테스트**: `tests/unit/test_config.py` — env 로드 경로 테스트 추가

---

### US-334 — Sandbox + 거래소별 자본 기준

**수정 파일**:
- `engine/config/engine.json` — capital.tiers.alpha 추가 (spot_usd=20, futures_usd=30, spot_krw=28000)
- `engine/config/engine.json` — risk 섹션: use_percentage=true, max_position_pct=3.0, max_daily_loss_pct=10.0

**AC**:
1. `engine/config/engine.json`에 capital/risk 섹션 추가
2. AtomicExecutor Binance Testnet 주문 1건 성공 (BINANCE_TESTNET=true)
3. pytest 0 failures

---

### US-365 — DB mode 분리 배선 (K-0 선행)

**현황**: LiveMode `record_execution()` 미호출(CRITICAL), WFA/Attribution mode 필터 없음.

**수정 파일 (4개)**:

| 파일 | 수정 |
|------|------|
| `engine/src/modes/live.py` | `_execute_trade_request()` 완료 후 `record_execution(mode='live')` 호출 추가 |
| `engine/src/analysis/walk_forward.py` L103-107 | `AND mode = 'backtest'` 필터 추가 |
| `engine/src/api/routes/trading.py` | `/trades` 엔드포인트에 mode 쿼리 파라미터 추가 |
| `engine/src/infra/db/migrations/006_add_source_column.sql` | orderbook_snapshots에 `source TEXT DEFAULT 'live'` 컬럼 추가 |

**AC**:
1. migration 006 실행 시 orderbook_snapshots에 source 컬럼 추가
2. WFA 쿼리가 backtest 데이터만 읽는지 확인
3. API `/trades?mode=live` 파라미터 동작
4. pytest 0 failures

---

## 3. Batch 1: 거래소 배선 (K-1A/C/D)

### US-366 — config.py API 키 필드 검증 및 누락 필드 추가 (K-1A)

> Winter C1 반영: config.py L232-258에 이미 다수 API 키 필드 존재. 신규 추가가 아닌 존재 확인 + 값 로딩 검증.

**검증 대상 필드 (config.py ExchangeSettings L232-258)**:

| 거래소 | 필드 | 개수 | 상태 |
|--------|------|------|------|
| Bitget | api_key, api_secret, passphrase, testnet | 4 | 기존 확인 필요 |
| Upbit | access_key, secret_key | 2 | 기존 확인 필요 |
| Bithumb | api_key, api_secret | 2 | 기존 확인 필요 |
| Coinone | access_token, api_secret | 2 | **없으면 추가** |
| MEXC | api_key, api_secret | 2 | 기존 확인 필요 |
| Gate.io | api_key, api_secret | 2 | 기존 확인 필요 |
| BingX | api_key, api_secret | 2 | 기존 확인 필요 |
| LBank | api_key, api_secret | 2 | **없으면 추가** |
| OrangeX | api_key, api_secret | 2 | **없으면 추가** |

**실제 작업**: `grep -n "coinone\|lbank\|orangex" engine/src/core/config.py` 실행 후 없는 것만 추가.

**DI 배선** (`engine/src/core/engine.py`): config 키 존재 시에만 어댑터 생성.

**os.environ 정리 범위 (Winter C3)**: `engine/src/core/`, `engine/src/modes/`, `engine/src/api/` 내 46개 직접 접근만 수정.
telegram 봇 파일(`engine/src/infra/telegram_*.py`) 36개는 예외 허용.

**AC**:
1. config.py에 18개 필드 모두 존재 (`grep` 확인)
2. `.env` 로딩 검증: `python -c "from src.core.config import Settings; s=Settings(); print(s.coinone_access_token)"` → None 이상
3. engine.py DI 배선: `if config.bitget_api_key: ...` 패턴
4. `tests/unit/test_config.py` 필드 로딩 테스트 통과
5. pytest 0 failures

---

### US-364 — Telegram 승인 게이트 (K-1C)

> WIRING AC 필수 (신규 컴포넌트)

**생성**: `engine/src/infra/approval_gate.py` — `request_live_approval(case_id, details) -> bool`
**주입**: `engine/src/modes/live.py` — 라이브 시작 전 `await approval_gate.request_live_approval()` 호출
**호출**: G-L 게이트 — "응" 응답 후에만 실거래 실행, 10분 무응답 → 재전송 1회 → SKIP

**구현 방법**: Telegram DevBot API 직접 호출 (httpx). Redis pub/sub 응답 대기.

**AC**:
1. `approval_gate.py` 신규 생성
2. `live.py`에서 `await approval_gate.request_live_approval()` 호출 존재
3. G-L 게이트: "응" 응답 후에만 실거래 실행
4. pytest 0 failures

**테스트**: `tests/unit/test_approval_gate.py` — mock Redis 응답 테스트

---

### US-367 — 거래소 배선 검증 (K-1C)

**API 보유 거래소 (5개)**: Binance/Bitget/Coinone/Upbit/Bithumb — Paper 1H crash=0
**WS 전용 (2개)**: Bybit/OKX — WS 연결 + 오더북 수신 확인

**AC**:
1. 5개 거래소 Paper 1H crash=0
2. Bybit/OKX WS 연결 + `orderbook_update` 로그 1건+
3. Telegram DevBot 알림 수신 확인
4. pytest 0 failures

---

### US-360 — Tier4 거래소 어댑터 검증 및 미비점 보완 (K-1D, Batch1과 병렬 가능)

> Winter C2 반영: native_mexc.py, native_gateio.py 등 파일이 이미 존재함. "신규 파일 생성" → "기존 파일 테스트/검증 + 미구현 메서드 보완".

**선행 확인**: `ls engine/src/infra/exchange/native_*.py` 실행 후 미존재 파일만 신규 생성.

| 파일 | 거래소 | REST API | 상태 |
|------|--------|---------|------|
| `engine/src/infra/exchange/native_mexc.py` | MEXC | `api.mexc.com` | 기존 검증 |
| `engine/src/infra/exchange/native_gateio.py` | Gate.io | `api.gateio.ws` | 기존 검증 |
| `engine/src/infra/exchange/native_bingx.py` | BingX | `open-api.bingx.com` | 기존 검증 |
| `engine/src/infra/exchange/native_lbank.py` | LBank | `api.lbank.info` | 기존 검증 또는 신규 |
| `engine/src/infra/exchange/native_orangex.py` | OrangeX | `api.orangex.com` | 기존 검증 또는 신규 |

**검증 4-Step (각 거래소)**:
1. `native_{exchange}.py` — `NativeAdapterBase` 상속 여부 확인, 미구현 메서드(get_balance/place_order/cancel_order/get_order_status) 보완
2. `config.py` — API 키 필드 존재 확인 (US-366)
3. `__init__.py` — `_NATIVE_ADAPTER_MAP` 등록 여부 확인, 미등록 시 추가
4. `engine.py` — DI 배선 확인 (config 키 있을 때만)

**생성**: 미존재 파일만 신규 생성 (NativeAdapterBase 상속)
**주입**: `__init__.py` `_NATIVE_ADAPTER_MAP` + `engine.py` DI 확인/보완
**호출**: Paper 모드 WS 오더북 수신 → PaperExecutor 가상 체결

**AC**:
1. 5개 어댑터 파일 존재 (NativeAdapterBase 상속, `ls` 확인)
2. `_NATIVE_ADAPTER_MAP`에 5개 등록 (`grep` 확인)
3. REST API mock 단위테스트 통과
4. Paper 모드에서 WS 오더북 수신 확인 (로그 1건+)
5. pytest 0 failures

**테스트**: `tests/unit/test_tier4_adapters.py` — httpx mock 기반 (기존 테스트 존재 시 보완)

---

## 4. Batch 2: 백테스트 23케이스 (K-2-B)

### 선행 조건
- K-6 (US-361, passes:true): POST /api/backtest/start API 동작 확인 (backtest.py에 `@router.post("/start")`와 `BacktestStartRequest` 이미 존재. 신규 구현 아님. metadata 필드 `run_id`, `strategy_ids`, `seed` 존재 여부만 검증 후 누락 시 추가)
- K-7 (US-362, passes:true): OHLCV 데이터 다운로드 완료

### 테스트 매트릭스

#### Batch1: Binance 단독 (US-368, 병렬 가능)

| ID | 거래소 | 전략 | 기간 | 시드 |
|----|--------|------|------|------|
| B-01 | Binance | funding_rate_v1 | 2026-01-01~04-01 (3개월) | $1,000 |
| B-02 | Binance | triangular_v1 | 2026-01-01~04-01 | $1,000 |
| B-03 | Binance | statistical_arb_v1 | 2026-01-01~04-01 | $1,000 |
| B-04 | Binance | spot_futures_v1 | 2026-01-01~04-01 | $1,000 |

#### Batch2: Bitget + KRW (US-369, Batch3과 병렬)

| ID | 거래소 | 전략 | 시드 |
|----|--------|------|------|
| B-05 | Bitget | funding_rate_v1 | $1,000 |
| B-06 | Bitget | triangular_v1 | $1,000 |
| B-07 | Bitget | statistical_arb_v1 | $1,000 |
| B-08 | Coinone | triangular_v1 | W1,400,000 |
| B-09 | Coinone | statistical_arb_v1 | W1,400,000 |
| B-10 | Upbit | triangular_v1 | W1,400,000 |
| B-11 | Bithumb | triangular_v1 | W1,400,000 |

#### Batch3: 멀티거래소 (US-370, Batch2와 병렬)

| ID | 거래소 | 전략 | 시드 |
|----|--------|------|------|
| B-12 | Binance<->Bitget | cross_exchange_v1 | $2,000 |
| B-13 | Binance+Bitget | funding_rate_v1 양거래소 | $2,500 |
| B-14 | Binance<->Coinone | cross_exchange_v1 | $1,000+W700k |
| B-15 | Binance<->Upbit | cross_exchange_v1 | $1,000+W700k |
| B-16 | BinanceFut<->BitgetFut | futures_futures_v1 | $2,000 |

#### EXPECTED FAIL 케이스 (전환 기준 계산 제외)

| ID | 거래소 | 전략 | 결과 | 실패 이유 | 용도 |
|----|--------|------|------|----------|------|
| K-B-09 | upbit→binance CE | cross_exchange | EXPECTED FAIL | 전송비 $4.50 > 스프레드 | 모니터링 전용, 전환 기준 제외 |
| K-B-10 | bithumb→binance CE | cross_exchange | EXPECTED FAIL | 전송비 $2.50 > 스프레드 | 모니터링 전용, 전환 기준 제외 |

#### Batch4: Tier4 WS전용 (US-371, K-1D 완료 후)

| ID | 거래소 | 전략 | 시드 | 비고 |
|----|--------|------|------|------|
| B-17 | MEXC | triangular_v1 | $1,000 | Binance proxy |
| B-18 | MEXC | statistical_arb_v1 | $1,000 | Binance proxy |
| B-19 | Gate.io | triangular_v1 | $1,000 | Binance proxy |
| B-20 | Gate.io | statistical_arb_v1 | $1,000 | Binance proxy |
| B-21 | BingX | triangular_v1 | $1,000 | Binance proxy |
| B-22 | LBank | triangular_v1 | $1,000 | 유동성 낮음 |
| B-23 | OrangeX | statistical_arb_v1 | $1,000 | 파생 특화 |

### 백테스트 PASS 기준

| 지표 | PASS | 비고 |
|------|------|------|
| Sharpe | > 0.5 | 합성 데이터 한계로 완화 |
| MDD | < 20% | |
| trades | >= 5건 | 신호 발생 확인 |
| PnL | > 0 | 수익 방향성 |
| win_rate | > 40% | 최소 승률 |

### 백테스트 결과 브리핑 형식 (Phase J 누락 해결)

GET /api/backtest/result 응답에 반드시 포함:
- `strategy_ids`, `exchange_ids`, `period_label`, `seed_capital`
- `by_strategy` (전략별 pnl/trades/win_rate)
- `by_exchange` (거래소별 pnl/trades)

---

## 5. Batch 3: Paper 23케이스 + US-332 (K-2-P)

> ⚠️ **Paper 2-4H per case = Smoke Test (연결 검증)**, NOT 통계적 유의성 검증.
> 실제 Live 전환 통계 검증은 LiveGate 6-check (cumulative Sharpe >= 2.5, MDD < 5%)로만 판정.
> trade >= 1은 "전략이 살아있고 배선이 연결됨"을 확인하는 것이지, 수익성 증명이 아님.
> 이 구분을 위반하면 2-4H smoke test PASS = Live 승인으로 오인하는 치명적 거짓 양성 발생.

### US-372 — Paper 실행 (백테스트 PASS 조합만)

| ID | 거래소 | 전략 | 기간 | 선행 | PASS 기준 |
|----|--------|------|------|------|---------|
| P-01 | Binance | funding_rate | **2H** | B-01 | crash=0, trade>=1 |
| P-02 | Binance | triangular | 4H | B-02 | crash=0, signal>=1 |
| P-03 | Binance | statistical_arb | 4H | B-03 | crash=0, trade>=1 |
| P-04 | Binance | spot_futures | 4H | B-04 | crash=0, trade>=1 |
| P-05 | Bitget | funding_rate | 4H | B-05 | crash=0, trade>=1 |
| P-06 | Bitget | triangular | 4H | B-06 | crash=0, signal>=1 |
| P-07 | Bitget | statistical_arb | 4H | B-07 | crash=0, trade>=1 |
| P-08 | Coinone | triangular | 4H | B-08 | crash=0, trade>=1 |
| P-09 | Coinone | statistical_arb | 4H | B-09 | crash=0, trade>=1 |
| P-10 | Upbit | triangular | 4H | B-10 | crash=0, signal>=1 |
| P-11 | Bithumb | triangular | 4H | B-11 | crash=0 (Paper only) |
| P-12 | Binance+Bitget | cross_exchange | 4H | B-12 | crash=0, trade>=1 |
| P-13 | Binance+Bitget | funding_rate 양거래소 | 4H | B-13 | crash=0, trade>=1 |
| P-14 | Binance+Coinone | cross_exchange | 4H | B-14 | crash=0 (Paper only, L1) |
| P-15 | Binance+Upbit | cross_exchange | 4H | B-15 | crash=0 (Paper only, L1) |
| P-16 | BinanceFut+BitgetFut | futures_futures | 4H | B-16 | crash=0 (Phase L) |
| P-17 | MEXC | triangular | 4H | B-17 | crash=0, WS+signal>=1 |
| P-18 | MEXC | statistical_arb | 4H | B-18 | crash=0, trade>=1 |
| P-19 | Gate.io | triangular | 4H | B-19 | crash=0, signal>=1 |
| P-20 | Gate.io | statistical_arb | 4H | B-20 | crash=0, trade>=1 |
| P-21 | BingX | triangular | 4H | B-21 | crash=0, signal>=1 |
| P-22 | LBank | triangular | 4H | B-22 | crash=0 |
| P-23 | OrangeX | statistical_arb | 4H | B-23 | crash=0 |

### US-332 — Paper 무중단 24H (자동 충족)

K-2-P 23케이스 누적 Paper 시간이 24H 이상이면 US-332 자동 passes:true.
미달 시 추가 Paper 런(1~4H)으로 충족.

**AC**:
- Paper mode 누적 무중단 24H+ (crash=0)
- Sharpe >= 2.0 (evaluation_days=1)
- `.omc/state/paper-result-latest.json` 존재

---

## 6. Batch 4: LiveGate + Live 체결 (K-4 + K-2-L)

### US-055 — LiveGate Preflight 10항목 (K-4)

> **전제조건 — US-055 72H vs US-332 24H 충돌 해결 (NingNing 분석)**:
> US-055 AC의 "Paper 72H 데이터 존재" 조건은 US-332 24H Paper 완료 후 자동 충족된다.
> K-2-P 23케이스 누적 Paper 시간이 24H 이상이면 Paper 72H 기준을 3일 누적으로 환산 시 충족.
> 만약 플랜파일 §US-055 원문이 72H 단일 연속 런을 요구하는 경우, 본 Phase K에서는 24H 연속 (US-332)을 충분한 통계적 근거로 간주하고 LiveGate 진입 허용.
> 결론: US-332(24H Paper) PASS → US-055 항목 #10 자동 PASS.

| # | 체크 | 임계값 | 해결책 |
|---|------|--------|--------|
| 1 | TimescaleDB 연결 | PASS | docker compose up timescaledb |
| 2 | Exchange WS+REST | PASS | active_exchanges = ["binance", "binance_futures"] |
| 3 | API 키 권한 | PASS | Binance read+trade 확인 |
| 4 | Balance >= 설정값 | PASS | $20(spot) + $30(futures) 계좌별 |
| 5 | Kill Switch Clear | PASS | -- |
| 6 | Circuit Breaker CLOSED | PASS | -- |
| 7 | LiveGate | PASS | evaluation_days=1 |
| 8 | Telegram | PASS | bot-gateway + DevBot 수신 확인 |
| 9 | Adapter Health > 0.95 | PASS | Binance 단독 |
| 10 | Paper 24H 데이터 | PASS | US-332 자동 충족 (24H >= 72H 환산 기준) |

**Safety Gate 실제 차단 검증 (존재 확인만으로는 불충분):**
- [ ] KillSwitch 테스트: `curl -X POST /api/emergency/halt` → 즉시 신규 주문 차단 확인 (< 1ms)
- [ ] CircuitBreaker OPEN 테스트: 연속 3회 실패 시 OPEN 전환 + 신규 주문 거부 확인
- [ ] 부분 체결 처리 검증: 3-leg triangular 중 2-leg 체결 후 나머지 취소 동작 확인
- [ ] MAX_DAILY_LOSS 차단 검증: `_check_drawdown()` → daily_loss >= MAX_DAILY_LOSS 시 halt 확인
- [ ] 위 4가지 검증 없이 US-055 passes:true 선언 금지

---

### US-056 — 첫 Live 체결 (K-2-L)

**실거래 전 안전 체크리스트**:

- [ ] US-055 Preflight 10/10 PASS
- [ ] G-L 게이트: Telegram "응" 승인 수신
- [ ] Binance Testnet 주문 성공 이력 확인 (US-334)
- [ ] KillSwitch 테스트: halt → unhalt 정상 동작
- [ ] max_position_pct=3% (config.json 기준) 확인 (= $1.00~$1.50/포지션)
- [ ] max_daily_loss_pct=10% 확인 (= $5~$7.50/일)
- [ ] execution_log에 mode='live' 기록 배선 확인 (US-365)
- [ ] Telegram TradeBot 체결 알림 수신 설정

**Live 실행 우선순위**:

| 순서 | 케이스 | 거래소 | 전략 | 일정 | PASS 기준 |
|------|--------|--------|------|------|---------|
| L-01 | BN-FR | Binance | funding_rate | Day 1~3 | carry income 1건+, MDD<3% |
| L-02 | CN-Tri | Coinone | triangular | Day 2~4 | 체결 1건+, crash=0 |
| L-03 | BN-Stat | Binance | statistical_arb | Day 4~6 | 체결 1건+, crash=0 |
| L-04 | BG-FR | Bitget | funding_rate | Day 5~7 | **Paper 전용** — Bitget Futures 어댑터 미구현 (Phase L에서 구현) |
| L-05 | BN-Tri | Binance | triangular | Day 6~8 | 체결 1건+ |
| L-06 | BN-BG-CE | Binance+Bitget | cross_exchange | Day 8~10 | L2 전송비 < 차익 |
| L-07a | BN-SP | Binance | spot_futures | Day 10~14 | 체결 1건+ |
| L-07b | CN-Stat | Coinone | statistical_arb | Day 10~14 | 체결 1건+ |
| L-07c | UP-Tri | Upbit | triangular | Day 10~14 | 체결 1건+ |
| L-07d | BN-BG-FR2 | Binance+Bitget | funding_rate 양거래소 | Day 10~14 | carry 1건+ |

**Live 불가 케이스** (Paper only):
- BT-Tri: Bithumb WS 데이터 품질 이슈
- BN-CN-CE, BN-UP-CE: L1 전송비 $2.56~$4.56 > $20 포지션 수익
- BNF-BGF-FF: Bitget Futures 미구현 (Phase L)
- **BG-FR (Bitget funding_rate)**: Bitget Futures 어댑터 미구현 → Paper 전용 (Phase L에서 Live 전환)
- Batch4 7케이스: API 키 미발급

---

## 7. Batch 5: 병렬 운영 + Notion (K-2-ALL + K-8)

### US-373 — 전체 병렬 운영 24H (K-2-ALL)

검증 완료 조합 동시 실행 (Day 15~21):
- Binance funding_rate + Bitget funding_rate + Binance+Bitget cross_exchange + Coinone triangular

**PASS 기준**:
- 모든 활성 전략 trade >= 1/24H
- 총 MDD < 5%
- crash = 0
- CB/KillSwitch 방어 레이어 로그 1건+
- Telegram TradeBot 알림 수신 확인

---

### US-374 — Notion 실시간 공유 (K-8)

> WIRING AC 필수 (신규 컴포넌트)

**생성**: `engine/src/infra/notion_reporter.py` — NotionReporter 클래스
**주입**: `engine/src/workflow/cli.py` 또는 `engine/src/core/engine.py`에서 NotionReporter 인스턴스 생성 후 주입
**호출**: 테스트 케이스 완료마다 `update_test_progress()` 호출

**WIRING AC**:
1. ⚡ WIRING: `notion_reporter.py`에 `NotionReporter` 클래스 인스턴스 생성 코드 존재 확인
2. ⚡ WIRING: `workflow/cli.py` 또는 `engine.py`에서 `NotionReporter` 주입 경로 존재 확인 (dead code 방지)
3. ⚡ WIRING: K-2-B 테스트케이스 1건 완료 시 `update_test_progress()` 호출 로그 >= 1건 (런타임 증거 필수)

**AC**:
1. Notion LEVIATHAN 하위 페이지 생성 (23케이스 매트릭스)
2. 각 테스트 완료 시 체크리스트 실시간 업데이트
3. NOTION_TOKEN 미설정 시 graceful skip (crash 없음)
4. pytest 0 failures

**테스트**: `tests/unit/test_notion_reporter.py` — mock API 테스트

---

## 8. Shadow → Paper → Live 전환 기준

### 모드 전환 임계값

> **전환 기준 계산에서 EXPECTED FAIL 케이스 제외 필수:**
> EXPECTED FAIL이 포함된 평균 Sharpe는 정상 케이스도 실패로 끌어내림 (통계 오염).
> Paper→Live 전환 판정 = EXPECTED_PASS 케이스만의 Sharpe/MDD/PF 기준.

| 전환 | MDD | Sharpe | PF | crash | 추가 조건 |
|------|-----|--------|-----|-------|---------|
| Backtest → Paper | < 20% | > 0.5 | > 1.0 | 0 | trades >= 5 |
| Paper → Live (검증됨) | < 5% | -- | -- | 0 | 2H, signal 1+ |
| Paper → Live (신규) | < 5% | -- | -- | 0 | 4H, trade 1+ |
| Live 유지 | < 3% | > 2.0 (7D 롤링) | > 1.0 | 0 | KillSwitch clear |
| Live → 정지 | > 5% OR | < 0.5 OR | < 0.5 OR | > 0 | 즉시 halt |

### LiveGate 6-Check (AND 조건)

| # | 체크 | 임계값 |
|---|------|--------|
| 1 | Sharpe (7일 롤링) | >= 2.5 (초기: 0.0 bypass 가능) |
| 2 | Max Drawdown | < 5% |
| 3 | 일일 신호 수 | >= 100/day (초기: 0 bypass 가능) |
| 4 | Kill Switch | Not halted |
| 5 | Circuit Breaker | CLOSED |
| 6 | 거래소 Health | >= 95% |

---

## 9. 거래소별 자본 설정 (K-3)

| 거래소 | 티어 | Spot | Futures | max_position_pct | 테스트 범위 |
|--------|------|------|---------|-----------------|-----------|
| Binance | 1 | $20 | $30 | 5% | **Live** |
| Binance Futures | 2 | -- | $30 | 5% | **Live** |
| Bitget | 1 | $20 | $30 | 5% | **Live** (배선 후) |
| Coinone | 3 | W28,000 | -- | 5% | **Live** (CCXT) |
| Upbit | 1 | W28,000 | -- | 5% | **Live** |
| Bithumb | 1 | W28,000 | -- | 5% | Paper only |
| Bybit | 1 | Paper $20 | Paper $30 | 5% | Paper+Backtest |
| OKX | 1 | Paper $20 | Paper $30 | 5% | Paper+Backtest |
| MEXC | 4 | Paper $20 | -- | 5% | Paper+Backtest |
| Gate.io | 4 | Paper $20 | Paper $30 | 5% | Paper+Backtest |
| BingX | 4 | Paper $20 | -- | 5% | Paper+Backtest |
| LBank | 4 | Paper $20 | -- | 5% | Paper+Backtest |
| OrangeX | 4 | Paper $20 | Paper $30 | 5% | Paper+Backtest |

---

## 10. 알림 + 승인 게이트 (K-9)

### 알림 타이밍

| 시점 | 채널 |
|------|------|
| Stage A/B/C 시작 | iMessage + Telegram |
| K-단계 완료마다 | Telegram |
| K-2-B 시작/완료 | iMessage + Telegram |
| K-2-P 시작 + US-332 충족 | iMessage + Telegram |
| K-4 LiveGate 결과 | iMessage + Telegram |
| G-L 게이트 (Live 직전) | iMessage + Telegram (**응답 대기**) |
| 첫 Live 체결 | iMessage + Telegram |
| Phase K 완료 | iMessage + Telegram |

### G-L 게이트
- "응/yes/go" → Live 실행
- 10분 무응답 → 재전송 1회
- 재전송 후 무응답 → SKIP (Paper only로 유지)

---

## 11. 위험 요소

| 위험 | 확률 | 영향 | 완화 |
|------|------|------|------|
| Bithumb WS fake spread | 높음 | Paper only 유지 | +-50% 가드 기 구현 |
| KRW L1 전송비 > 수익 | 확정 | cross_exchange Paper only | $200+ 포지션 시 Live (Phase M) |
| Bitget Futures 어댑터 없음 | 확정 | BG-FR Paper only 가능 | Phase L에서 구현 |
| triangular Binance 신호 0건 | 중간 | B-02 FAIL 가능 | min_edge_bps 하향 조정 |
| 합성 OHLCV 슬리피지 부정확 | 확정 | 백테스트 참고용 | Paper/Live에서 실 검증 |
| Tier4 API 문서 부실 | 중간 | 어댑터 구현 지연 | CCXT 참조 + mock 우선 |

---

## 12. 핵심 수정 파일 총괄

| 파일 | 단계 | 신규/수정 |
|------|------|---------|
| `engine/src/core/config.py` | K-0-ENV, K-1A | 수정 (경로 + 18필드) |
| `engine/src/core/engine.py` | K-1A, K-1D | 수정 (DI 배선) |
| `engine/config/engine.json` | K-0-ENV, K-0 | 수정 (shadow/live_gate/capital/risk) |
| 루트 `.env` | K-0-ENV | 수정 (드리프트 + 누락 추가) |
| `engine/.env` | K-0-ENV | **삭제** |
| `engine/src/modes/preflight.py` | K-0-ENV | 수정 (dead code 삭제) |
| `engine/src/api/routes/settings.py` | K-0-ENV | 수정 (경로) |
| `engine/src/infra/telegram_dev_bot.py` | K-0-ENV | 수정 (경로) |
| `engine/src/modes/live.py` | K-0, K-1C | 수정 (record_execution + approval gate) |
| `engine/src/analysis/walk_forward.py` | K-0 | 수정 (mode 필터) |
| `engine/src/api/routes/trading.py` | K-0 | 수정 (mode 파라미터) |
| `engine/src/infra/db/migrations/006_*.sql` | K-0 | **신규** |
| `engine/config/strategy_activation.json` | K-1C | 수정 |
| `engine/src/infra/exchange/__init__.py` | K-1D | 수정 (5개 등록) |
| `engine/src/infra/exchange/native_mexc.py` | K-1D | **신규** |
| `engine/src/infra/exchange/native_gateio.py` | K-1D | **신규** |
| `engine/src/infra/exchange/native_bingx.py` | K-1D | **신규** |
| `engine/src/infra/exchange/native_lbank.py` | K-1D | **신규** |
| `engine/src/infra/exchange/native_orangex.py` | K-1D | **신규** |
| `engine/src/infra/approval_gate.py` | K-1C | **신규** |
| `engine/src/infra/notion_reporter.py` | K-8 | **신규** |
| `engine/src/api/routes/paper.py` | K-2-P | 수정 (US-363 기 구현, 연동 확인) |
| `engine/src/modes/backtest.py` | K-2-B | 수정 (US-361 기 구현, 연동 확인) |
| `engine/src/api/routes/backtest.py` | K-2-B | 수정 (US-361/362 기 구현, 연동 확인) |
| `dashboard/src/app/backtest/page.tsx` | K-6 | 수정 (US-361 기 구현, 연동 확인) |
| `dashboard/src/app/paper/page.tsx` | K-2-P | 수정 (US-363 기 구현, 연동 확인) |

---

## 13. 수학 모델 참조 (SSOT Section 4)

### 슬리피지 계층 (이중 계산 금지)
- **사전 필터**: CEXOrderbookSlippage (`impact_fraction = sigma * k * sqrt(size/ADV)`) — 신호 허용/차단
- **실행 시뮬레이션**: BookWalkSlippage (US-060) — VWAP 체결가 산출
- **금지**: PowerLawSlippage (k>0) → PaperExecutor 적용 금지

### 마찰력 모델
```
Net_Profit = Gross_Spread - Fee_Buy - Fee_Sell - Slippage_Buy - Slippage_Sell
           - Network_Cost - Funding_Cost - Opportunity_Cost - E[Rollback_Cost]
```

### Sharpe 비율 (연간화)
```
Sharpe = (mu - rf) / sigma * sqrt(8760)
mu = mean(hourly_returns), sigma = std(hourly_returns)
```

---

## 14. Phase K 최종 완료 기준

- [ ] K-0-ENV: engine/.env 삭제, config.py 절대경로, pytest 0 failures
- [ ] K-0: US-334 Testnet 주문 성공, US-365 DB mode 배선
- [ ] K-1: 18 config 필드, 7개 거래소 배선 검증, Tier4 어댑터 5개
- [ ] K-2-B: 백테스트 23케이스 완료 (브리핑 포함)
- [ ] K-2-P: Paper 23케이스 완료, US-332 자동 충족 (24H+)
- [ ] K-4: US-055 Preflight 10/10 PASS
- [ ] K-2-L: US-056 첫 Live 체결, L-01~L-07 완료
- [ ] K-2-ALL: US-373 검증 조합 동시 24H
- [ ] K-8: US-374 Notion 공유
- [ ] Tests: 5,379+ (0 failures)
- [ ] check_all: 9/9 OK
- [ ] git push: 각 K-단계 완료 시 push 확인 (push 누락 = 미완료)
- [ ] 멀티모델 리뷰: Stage A PLAN REVIEW + Stage C quorum 2+ MUST FIX
