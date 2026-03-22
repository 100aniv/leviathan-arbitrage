# LEVIATHAN Engine — 파이썬 고빈도 거래 엔진

**engine/** 폴더는 LEVIATHAN의 핵심 로직을 포함합니다. 실시간 시세 수집, 신호 생성, 주문 실행, 리스크 관리를 AsyncIO 기반으로 처리합니다.

## 엔진 구조

### 메인 모듈 역할

| 모듈 | 파일 | 설명 |
|------|------|------|
| **main** | `src/main.py` | 엔진 라이프사이클 (설정 → 초기화 → 실행 → 종료) |
| **collectors** | `src/collectors/` | 10개 거래소 WebSocket 수집기 (실시간 시세, 펀딩율) |
| **strategies** | `src/strategies/` | 7개 차익거래 전략 (cross_exchange, spot_futures, ...) |
| **execution** | `src/execution/` | 주문 실행 + 자동화 프로토콜 (AtomicExecutor, PaperExecutor) |
| **risk** | `src/risk/` | Kill Switch, Circuit Breaker, 포지션 관리자 |
| **friction** | `src/friction/` | 수수료 모델 + 슬리피지 모델 |
| **core** | `src/core/` | 공유 타입 (Signal, Trade, Order, Settings) |
| **api** | `src/api/` | FastAPI 서버 (REST + WebSocket + JWT) |
| **infra** | `src/infra/` | Redis, TimescaleDB, Telegram 통합 |
| **workflow** | `src/workflow/` | 체크포인트 + 일관성 검사 (순수 Python) |
| **ml** | `src/ml/` | HMM 레짐 분류, XGBoost 학습, ONNX 추론 |
| **modes** | `src/modes/` | Shadow 모드 메트릭 (PnL, Win Rate, Sharpe, MDD) |

### 데이터 흐름

```
Collectors (WebSocket)
    ↓
PriceHub (실시간 시세 캐시)
    ↓
CostCalculator (수수료 + 슬리피지 추정)
    ↓
SignalGenerator (스프레드 감지 → Signal)
    ↓
StrategyManager (7개 전략 필터링)
    ↓
RiskGuardian (Position Limit, Kill Switch)
    ↓
AtomicExecutor (주문 실행, 자동화 프로토콜)
    ↓
TradeConsumer (결과 처리, PnL 추적)
    ↓
ShadowMode / LiveGate (메트릭 평가)
```

## Collectors (거래소 데이터 수집)

### 지원 거래소

| 거래소 | Spot | Futures | 파일 |
|--------|------|---------|------|
| Binance | ✅ WebSocket | ✅ Futures | `binance_collector.py` / `binance_futures_collector.py` |
| Bybit | ✅ WebSocket | ✅ Futures | `bybit_collector.py` / `bybit_futures_collector.py` |
| OKX | ✅ WebSocket | ✅ Futures | `okx_collector.py` / `okx_futures_collector.py` |
| Bitget | ✅ WebSocket | — | `bitget_collector.py` |
| Upbit (한국) | ✅ WebSocket | — | `upbit_collector.py` |
| Bithumb (한국) | ✅ WebSocket | — | `bithumb_collector.py` |
| Coinone (한국) | ✅ WebSocket | — | `coinone_collector.py` |
| Funding Rate | ✅ 5개 거래소 | — | `funding_rate_collector.py` |

### 새 거래소 어댑터 추가

1. **BaseCollector 상속**
   ```python
   from src.collectors.base_collector import BaseCollector

   class MyExchangeCollector(BaseCollector):
       def __init__(self):
           super().__init__(exchange_id="my_exchange")

       async def _connect(self):
           # WebSocket 연결 로직
           pass

       async def _subscribe(self, symbols: list[str]):
           # 심볼 구독
           pass

       async def _handle_message(self, msg: dict):
           # 시세 메시지 파싱 → PriceHub에 발행
           self.price_hub.update_price(...)
   ```

2. **CollectorManager에 등록**
   ```python
   # src/collectors/manager.py
   from src.collectors.my_exchange_collector import MyExchangeCollector

   COLLECTORS = {
       "my_exchange": MyExchangeCollector,
       # ...
   }
   ```

3. **engine/.env에 추가**
   ```bash
   MY_EXCHANGE_API_KEY=your_key
   MY_EXCHANGE_SECRET=your_secret
   ```

## Strategies (차익거래 전략)

### 활성 전략 (7개)

| # | 전략 | 파일 | 설명 | 상태 |
|---|------|------|------|------|
| 1 | cross_exchange | `strategies/cross_exchange.py` | 거래소 간 스프레드 | ✅ 활성 |
| 2 | spot_futures | `strategies/spot_futures.py` | 현물-선물 차익 | 🔄 대기 |
| 3 | futures_futures | `strategies/futures_futures.py` | 선물 거래소 간 스프레드 | ✅ 활성 |
| 4 | triangular | `strategies/triangular.py` | 3각 차익 (BTC/ETH/USDT) | 🔄 대기 |
| 5 | funding_rate | `strategies/funding_rate.py` | 펀딩율 수익 (숏+롱) | ✅ 활성 |
| 6 | statistical_arb | `strategies/statistical_arb.py` | 통계적 쌍거래 (HMM) | ✅ 활성 |
| 7 | cex_dex | `strategies/cex_dex.py` | CEX-DEX 스프레드 | ⏸️ 비활성 |

### 새 전략 추가

```python
from src.strategies.base import BaseStrategy, TradeRequest, TradeLeg
from src.core.models import OrderSide

class MyStrategy(BaseStrategy):
    def __init__(self, strategy_id: str, cost_calculator, shadow_mode=False):
        super().__init__(strategy_id, cost_calculator, shadow_mode)

    async def on_signal(self, signal) -> list[TradeRequest]:
        """신호 수신 시 주문 생성"""
        # 1. 신호 분석
        edge = signal.spread - signal.friction_cost

        # 2. 리스크 확인
        if edge < self.min_edge_bps:
            return []

        # 3. 주문 생성
        legs = [
            TradeLeg(
                exchange_id="binance",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                size=Decimal("0.1"),
            ),
            TradeLeg(
                exchange_id="okx",
                symbol="BTC/USDT",
                side=OrderSide.SELL,
                size=Decimal("0.1"),
            ),
        ]

        request = TradeRequest(
            strategy_id=self._strategy_id,
            legs=legs,
            expected_profit_usdt=Decimal("50"),
            confidence=0.95,
        )
        return [request]

    async def on_fill(self, trade):
        """주문 체결 시 호출"""
        self._metrics.fills_received += 1
        self._metrics.total_realized_pnl_usdt += trade.realized_pnl
```

**StrategyManager에 등록** (`src/strategies/manager.py`):
```python
strategies.append(
    MyStrategy(
        strategy_id="my_strategy",
        cost_calculator=cost_calculator,
    )
)
```

## 설정 가이드 (engine/.env)

### 필수 항목

```bash
# 실행 모드: dev|staging|prod|test
ENGINE_ENV=dev

# 거래소 API 키
BINANCE_API_KEY=your_binance_key
BINANCE_SECRET=your_binance_secret

OKX_API_KEY=your_okx_key
OKX_SECRET=your_okx_secret
OKX_PASSPHRASE=your_okx_passphrase

# (다른 거래소 키 필요 시 추가)

# 데이터베이스 (Docker 호스트용)
DATABASE_URL=postgresql+asyncpg://leviathan:leviathan@localhost:5432/leviathan
REDIS_URL=redis://localhost:6379/0

# 실행 모드
DATA_MODE=shadow          # synthetic|real_public|shadow|live
EXECUTION_MODE=paper      # paper|live
```

### 거래소별 선택 항목

```bash
# Bybit
BYBIT_API_KEY=your_key
BYBIT_SECRET=your_secret

# Upbit (한국)
UPBIT_API_KEY=your_key

# Bithumb (한국)
BITHUMB_API_KEY=your_key
BITHUMB_SECRET=your_secret

# Coinone (한국)
COINONE_API_KEY=your_key
COINONE_SECRET=your_secret
```

### 선택 항목

```bash
# Telegram 알림
TRADE_TELEGRAM_BOT_TOKEN=your_bot_token
DEV_TELEGRAM_BOT_TOKEN=your_dev_token
INFRA_TELEGRAM_BOT_TOKEN=your_infra_token

# 심볼 발견
TRADING_SYMBOLS=BTC,ETH,XRP  # 쉼표 분리 (미설정시 자동 발견)
MIN_EDGE_BPS=5               # 최소 스프레드 (bps)

# BTC 기준가 (포지션 사이징용)
BTC_REFERENCE_PRICE=50000

# ML 모델
DEX_RPC_URL=https://eth-mainnet.alchemyapi.io/v2/your_key  # CEX-DEX 활성화

# 리스크 설정
MAX_POSITION_USDT=100000
MAX_DRAWDOWN_PCT=5
```

## CLI 명령어

### 메인 엔진

```bash
# Shadow 모드 10분 실행
cd engine
timeout 600 python -m src.main

# 결과 확인
cat ../.omc/state/shadow-result-latest.json
```

### 워크플로우 유틸리티

```bash
# 일관성 검사 (SSOT ↔ PRD ↔ State)
python -m src.workflow.cli check_all

# 체크포인트 저장
python -m src.workflow.cli checkpoint save

# 체크포인트 복원
python -m src.workflow.cli checkpoint restore

# 체크포인트 이력
python -m src.workflow.cli checkpoint history
```

## 엔진 옵션

### 실행 모드 조합

```bash
# Paper 모드 + 합성 데이터 (백테스트)
DATA_MODE=synthetic EXECUTION_MODE=paper python -m src.main

# Paper 모드 + 실시간 데이터 (파이프라인 검증)
DATA_MODE=real_public EXECUTION_MODE=paper python -m src.main

# Shadow 모드 + 실시간 데이터 (수익성 검증)
DATA_MODE=shadow EXECUTION_MODE=paper python -m src.main

# Live 모드 (실거래)
DATA_MODE=real_authenticated EXECUTION_MODE=live python -m src.main
```

### 전략 활성화/비활성화

```bash
# 특정 전략 비활성화 (Shadow 모드용)
SHADOW_DISABLED_STRATEGIES=spot_futures,triangular python -m src.main
```

## 테스트

### 전체 테스트
```bash
cd engine
python -m pytest tests/ -x --tb=short
```

### 특정 모듈 테스트
```bash
# Collectors 테스트
python -m pytest tests/unit/collectors/ -v

# Strategies 테스트
python -m pytest tests/unit/strategies/ -v

# Execution 테스트
python -m pytest tests/unit/execution/ -v

# Integration 테스트 (Docker 필요)
python -m pytest tests/integration/ -v
```

### 커버리지 리포트
```bash
python -m pytest tests/ --cov=src --cov-report=html
# htmlcov/index.html 열기
```

## 디버깅

### 로그 수준 변경
```bash
# engine/.env에 추가
LOG_LEVEL=DEBUG

# 또는 코드에서
import logging
logging.basicConfig(level=logging.DEBUG)
```

### 특정 거래소만 테스트
```bash
# Binance만 활성화
ENABLED_EXCHANGES=binance python -m src.main
```

### Shadow 모드 메트릭 확인
```bash
# Shadow 실행 후
python -c "
import json
with open('../.omc/state/shadow-result-latest.json') as f:
    result = json.load(f)
    print(f'PnL: ${result[\"total_pnl\"]:.2f}')
    print(f'Win Rate: {result[\"win_rate\"]:.1%}')
    print(f'Max Drawdown: {result[\"max_drawdown\"]:.2%}')
    print(f'Sharpe Ratio: {result[\"sharpe_ratio\"]:.2f}')
"
```

## 주요 개념

### Signal vs TradeRequest

- **Signal**: 차익 기회 감지 (스프레드, 비용, 이윤)
- **TradeRequest**: Signal에서 생성한 주문 (실행 준비됨)

### Paper vs Live Executor

- **PaperExecutor**: 가상 거래 (실제 자본 없음, Shadow/테스트용)
- **Executor**: 실 거래 (API 키로 Live 모드)

### CostCalculator

모든 거래의 예상 비용 계산:
```python
cost = cost_calculator.estimate_cost(
    exchange_id="binance",
    symbol="BTC/USDT",
    side=OrderSide.BUY,
    size=Decimal("0.1"),
    price=Decimal("50000"),
)
# 수수료 + 슬리피지 = 수익성 검증
```

## 성능 최적화 팁

1. **메모리**: Redis 포지션 캐시 활용 (DB 쿼리 최소화)
2. **네트워크**: 웹소켓 풀 재사용 (연결 오버헤드 감소)
3. **CPU**: Rust hot-path (PyO3) 사용 (신호 생성 3배 빠름)
4. **I/O**: AsyncIO 최대 활용 (블로킹 호출 금지)

## 문제 해결

| 문제 | 해결책 |
|------|--------|
| "DB 연결 실패" | `docker compose ps` 확인, TimescaleDB 상태 점검 |
| "거래소 API 에러" | API 키 유효성, 권한 (거래 활성화) 확인 |
| "신호 생성 안 됨" | MIN_EDGE_BPS 값 낮추기, 거래소 이용 가능 여부 확인 |
| "메모리 누수" | 백그라운드 태스크 정리, WebSocket 재연결 로그 점검 |
| "Rust 빌드 실패" | `pip install -e .` (최신 버전 설치), Rust toolchain 업데이트 |

## 관련 문서

- [SSOT.md](../SSOT.md) — 전략 상세 + 아키텍처
- [../README.md](../README.md) — 프로젝트 개요 + 5분 시작 가이드
- [../dashboard/README.md](../dashboard/README.md) — 대시보드 API 연동
