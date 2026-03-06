# LEVIATHAN 설정 레퍼런스

모든 설정은 환경 변수로 제공됩니다. `.env` 파일 또는 쉘 환경에서 설정하세요.

## 핵심 설정

| 환경 변수 | 기본값 | 설명 |
|-----------|--------|------|
| `EXECUTION_MODE` | `paper` | 실행 모드: `paper`, `sandbox`, `live` |
| `CAPITAL_TIER` | `alpha` | 자본 단계: `alpha`, `beta`, `prod` |
| `CAPITAL_INITIAL_CAPITAL` | `70` | 초기 자본 (USDT) |

## 실행 모드

### Paper (기본값)
```bash
EXECUTION_MODE=paper
```
- API 키 불필요
- InMemoryEventBus 사용 (Redis 불필요)
- PaperExchangeAdapter로 합성 오더북 생성
- GBM(기하 브라운 운동) 가격 모델
- 즉시 시작 가능

### Sandbox
```bash
EXECUTION_MODE=sandbox
```
- 거래소 테스트넷 API 키 필요
- 실제 시장 데이터 수신
- PaperExecutor로 주문 시뮬레이션 (실제 주문 안됨)
- Redis 필요

### Live
```bash
EXECUTION_MODE=live
```
- 실제 거래소 API 키 필요
- 실제 주문 실행
- Redis + TimescaleDB 필요
- **주의: 실제 자금이 사용됩니다**

## 거래소 API 키

```bash
# Binance
BINANCE_API_KEY=your_api_key
BINANCE_SECRET=your_secret

# Upbit
UPBIT_API_KEY=your_api_key
UPBIT_SECRET=your_secret

# Bybit
BYBIT_API_KEY=your_api_key
BYBIT_SECRET=your_secret

# OKX
OKX_API_KEY=your_api_key
OKX_SECRET=your_secret
OKX_PASSWORD=your_passphrase

# Bithumb
BITHUMB_API_KEY=your_api_key
BITHUMB_SECRET=your_secret

# Coinone
COINONE_API_KEY=your_api_key
COINONE_SECRET=your_secret

# Bitget
BITGET_API_KEY=your_api_key
BITGET_SECRET=your_secret
BITGET_PASSWORD=your_passphrase
```

## 인프라스트럭처

```bash
# Redis
REDIS_URL=redis://localhost:6379/0

# TimescaleDB
DATABASE_URL=postgresql://leviathan:password@localhost:5432/leviathan

# API 서버
API_HOST=0.0.0.0
API_PORT=8000
```

## 리스크 관리

```bash
# 킬 스위치
KILL_SWITCH_MAX_DRAWDOWN_PCT=0.05    # 최대 손실 5%에서 자동 중단
KILL_SWITCH_MAX_LOSS_USDT=10.0       # $10 손실 시 중단

# 서킷 브레이커
CIRCUIT_BREAKER_ERROR_THRESHOLD=5    # 연속 5회 에러 시 차단
CIRCUIT_BREAKER_TIMEOUT_SECONDS=60   # 60초 후 재시도

# 포지션 한도
MAX_POSITION_SIZE_USDT=35.0          # 단일 포지션 최대 크기
MAX_TOTAL_EXPOSURE_USDT=100.0        # 총 노출 한도
```

## 전략 파라미터

```bash
# Cross-Exchange Arbitrage
STRATEGY_CROSS_EXCHANGE_MIN_SPREAD_BPS=10    # 최소 스프레드 (basis points)
STRATEGY_CROSS_EXCHANGE_ENTRY_THRESHOLD=0.001
STRATEGY_CROSS_EXCHANGE_EXIT_THRESHOLD=0.0005

# 공통
STRATEGY_FEE_RATE=0.001              # 수수료율 (0.1%)
STRATEGY_STOP_LOSS_PCT=0.02          # 손절 비율 (2%)
```

## 튜닝

```bash
# Walk-Forward Optimizer
TUNER_N_TRIALS=100                   # Optuna 시행 횟수
TUNER_TRAIN_PERIODS=60               # 훈련 윈도우 (캔들 수)
TUNER_VAL_PERIODS=20                 # 검증 윈도우 (캔들 수)
TUNER_SHADOW_DURATION_HOURS=24       # 섀도우 모드 기간
```

## Paper Trading 세부 설정

```bash
# PaperExchangeAdapter
PAPER_BASE_PRICE=50000               # 시작 가격
PAPER_VOLATILITY=0.0002              # 틱 당 변동성
PAPER_TICK_INTERVAL=0.1              # 틱 간격 (초)
PAPER_SPREAD_INJECTION_RATE=0.3      # 스프레드 주입 빈도
PAPER_SPREAD_INJECTION_BPS=50        # 주입 스프레드 (bps)
```

## Docker Compose

```bash
# 전체 스택 시작
docker compose up -d

# 엔진만 시작
docker compose up -d engine

# 로그 확인
docker compose logs -f engine
```

## Makefile 명령어

```bash
make help              # 사용 가능한 명령어 목록
make install           # 의존성 설치
make test              # 전체 테스트
make test-cov          # 커버리지 포함 테스트
make backtest          # 백테스트 (합성 데이터)
make backtest-optimize # 최적화 백테스트
make paper-trade       # 5분 페이퍼 트레이딩
make paper-trade-quick # 1분 페이퍼 트레이딩
make tune              # 자동 튜닝 (shadow 포함)
make tune-quick        # 빠른 튜닝
make lint              # 린트
make format            # 코드 포맷팅
```
