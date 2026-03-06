# LEVIATHAN 종합 사용자 가이드

## 목차

1. [소개](#1-소개)
2. [아키텍처](#2-아키텍처)
3. [설치](#3-설치)
4. [빠른 시작 (Paper Trading)](#4-빠른-시작)
5. [백테스트](#5-백테스트)
6. [자동 튜닝](#6-자동-튜닝)
7. [실전 거래](#7-실전-거래)
8. [API 레퍼런스](#8-api-레퍼런스)
9. [리스크 관리](#9-리스크-관리)
10. [문제 해결](#10-문제-해결)

---

## 1. 소개

LEVIATHAN은 글로벌 암호화폐 아비트라지 엔진입니다. 여러 거래소 간 가격 차이를 실시간으로 감지하고 자동으로 차익거래를 실행합니다.

### 핵심 특징

- **8가지 아비트라지 전략**: Cross-Exchange, Triangular, Spot-Futures, Funding Rate, Statistical, Latency, Futures-Futures, CEX-DEX
- **7개 거래소 지원**: Binance, Upbit, Bybit, OKX, Bithumb, Coinone, Bitget
- **DEX 지원**: Uniswap V3 (Ethereum, Arbitrum, Polygon, Base)
- **자동 튜닝**: Optuna 기반 Bayesian Walk-Forward 최적화
- **리스크 관리**: Kill Switch, Circuit Breaker, Position Limits
- **3단계 실행 모드**: Paper → Sandbox → Live

### 지원 거래소

| 거래소 | 유형 | 현물 | 선물 | 테스트넷 |
|--------|------|------|------|----------|
| Binance | CEX | O | O | O |
| Upbit | CEX | O | X | X |
| Bybit | CEX | O | O | O |
| OKX | CEX | O | O | O |
| Bithumb | CEX | O | X | X |
| Coinone | CEX | O | X | X |
| Bitget | CEX | O | O | O |
| Uniswap V3 | DEX | O | X | O |

---

## 2. 아키텍처

```
OrderBook → SignalGenerator → StrategyManager → TradeRequestConsumer
                                                        ↓
                                                  RiskGuardian
                                                        ↓
                                                  AtomicExecutor
                                                        ↓
                                                  ExchangeAdapter
```

### 파이프라인 흐름

1. **OrderBook 수집**: 각 거래소 어댑터가 실시간 오더북을 수신
2. **Signal 생성**: PriceHub + SignalGenerator가 아비트라지 기회를 감지
3. **Strategy 처리**: StrategyManager가 등록된 전략에 시그널을 라우팅
4. **Trade Request**: 전략이 TradeRequest(매수/매도 주문 쌍)를 생성
5. **Risk Check**: RiskGuardian이 포지션 한도, 손실 한도 등을 검증
6. **Execution**: AtomicExecutor가 동시에 양쪽 거래소에 주문 실행
7. **Reconciliation**: 체결 결과를 PositionManager에 기록

---

## 3. 설치

### 요구 사항

- Python 3.12+
- pip
- Docker & Docker Compose (선택, Sandbox/Live 모드용)

### 기본 설치

```bash
git clone https://github.com/100aniv/leviathan-arbitrage.git
cd leviathan-arbitrage/engine
pip install -e ".[dev]"
```

### Docker 설치 (Sandbox/Live 모드)

```bash
cd leviathan-arbitrage
docker compose up -d
```

서비스 구성:
- **engine**: 메인 트레이딩 엔진
- **redis**: 이벤트 버스 및 캐시
- **timescaledb**: 시계열 데이터 저장
- **grafana**: 모니터링 대시보드
- **prometheus**: 메트릭 수집

---

## 4. 빠른 시작

### Paper Trading (API 키 불필요)

Paper 모드는 합성 데이터로 전체 파이프라인을 실행합니다.

```bash
cd engine

# 1분 페이퍼 트레이딩
python -m src.cli.paper_runner --duration 60 --verbose

# 5분 페이퍼 트레이딩 + 리포트
python -m src.cli.paper_runner --duration 300 --report

# 거래 로그 CSV 저장
python -m src.cli.paper_runner --duration 300 --save-log trades.csv

# 리포트 JSON 저장
python -m src.cli.paper_runner --duration 300 --save-report report.json
```

### Paper Trading 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--duration` | 60 | 실행 시간 (초) |
| `--capital` | 70 | 초기 자본 (USDT) |
| `--injection-rate` | 0.4 | 아비트라지 기회 주입 빈도 |
| `--injection-bps` | 50 | 주입 스프레드 (basis points) |
| `--tick-interval` | 0.05 | 오더북 업데이트 간격 (초) |
| `--verbose` | false | 각 거래 출력 |
| `--report` | false | 상세 리포트 출력 |

---

## 5. 백테스트

### 합성 데이터 백테스트

```bash
# 기본 (2000 캔들)
python -m src.cli.backtest_cli --data synthetic

# 캔들 수 조정
python -m src.cli.backtest_cli --data synthetic --candles 5000

# 파라미터 조정
python -m src.cli.backtest_cli --data synthetic \
  --min-spread-bps 10 \
  --entry-threshold 0.001 \
  --exit-threshold 0.0003

# 결과 저장
python -m src.cli.backtest_cli --data synthetic --output results.json
```

### CSV 데이터 백테스트

CSV 파일 형식 (필수 컬럼):
```
time,open,high,low,close,volume
2024-01-01T00:00:00,50000,50100,49900,50050,100
```

```bash
python -m src.cli.backtest_cli --data ./data/btcusdt_1m.csv
```

### Walk-Forward 최적화

```bash
# 50 trials, 기본 윈도우
python -m src.cli.backtest_cli --data synthetic --optimize --trials 50

# 커스텀 윈도우 크기
python -m src.cli.backtest_cli --data synthetic --optimize \
  --trials 100 \
  --train-periods 120 \
  --val-periods 40
```

### 결과 해석

```
Backtest (synthetic)
  Total PnL:      $72.23    ← 총 손익
  Sharpe Ratio:   0.45      ← 위험 대비 수익 (>0 양호, >1 우수)
  Max Drawdown:   -28.86%   ← 최대 손실폭 (절대값이 작을수록 좋음)
  Win Rate:       17.2%     ← 승률
  Num Trades:     64        ← 총 거래 수
  Beta Gate:      [FAIL]    ← 프로덕션 기준 충족 여부
```

---

## 6. 자동 튜닝

### Walk-Forward 최적화

Optuna TPE(Tree-structured Parzen Estimator)를 사용한 Bayesian 최적화입니다.

```bash
# 기본 튜닝
python -m src.cli.tune_cli --data synthetic --trials 50

# 섀도우 평가 포함
python -m src.cli.tune_cli --data synthetic --trials 100 --shadow

# 특정 전략 튜닝
python -m src.cli.tune_cli --strategy triangular --trials 50
```

### 최적화 흐름

1. **데이터 분할**: 훈련/검증 윈도우로 분할 (Walk-Forward)
2. **파라미터 탐색**: 각 폴드에서 Optuna가 최적 파라미터 탐색
3. **검증**: 미래 데이터(Out-of-Sample)에서 성능 검증
4. **섀도우 평가**: 최적 파라미터를 기존 파라미터와 비교
5. **의사 결정**: APPLY (적용) / MONITOR (관찰) / REJECT (거부)

### 섀도우 모드

섀도우 모드에서는 최적화된 파라미터가 시그널만 수신하고 실제 거래를 실행하지 않습니다. 일정 기간 후 기존 파라미터와 성능을 비교합니다.

**판정 기준**:
- **APPLY**: Sim-Real 분산 < 5%, 수익률 분포 유의차 없음
- **MONITOR**: 분산은 양호하나 통계적 유의차 존재
- **REJECT**: Sim-Real 분산 > 5%

---

## 7. 실전 거래

### 단계적 전환

```
Paper → Sandbox → Live
```

### Sandbox 모드

테스트넷에서 실제 시장 데이터로 전략을 검증합니다.

```bash
# 거래소 API 키 설정 (테스트넷용)
export BINANCE_API_KEY=testnet_key
export BINANCE_SECRET=testnet_secret

# Sandbox 모드로 엔진 시작
EXECUTION_MODE=sandbox python -m src.main
```

### Live 모드

```bash
# 실제 API 키 설정
export BINANCE_API_KEY=real_key
export BINANCE_SECRET=real_secret

# Live 모드로 엔진 시작 (주의!)
EXECUTION_MODE=live python -m src.main
```

**Live 모드 체크리스트**:
- [ ] Sandbox에서 최소 24시간 무사고 운영
- [ ] Beta Gate 기준 충족 (PnL > 0, PF > 1.2, MDD < 2%)
- [ ] Kill Switch 한도 설정 완료
- [ ] 소액으로 시작 ($70 권장)
- [ ] 모니터링 대시보드 확인

---

## 8. API 레퍼런스

### 상태 조회

```bash
# 엔진 상태
GET /api/v1/status

# 포지션 목록
GET /api/v1/positions

# PnL 요약
GET /api/v1/pnl
```

### 전략 관리

```bash
# 전략 목록
GET /api/v1/strategies

# 전략 토글
POST /api/v1/strategies/{strategy_id}/toggle

# 전략 설정 변경
POST /api/v1/strategies/{strategy_id}/config
Content-Type: application/json
{"min_spread_bps": 15}
```

### 리스크 관리

```bash
# 킬 스위치 활성화
POST /api/v1/risk/halt

# 킬 스위치 해제
POST /api/v1/risk/clear

# 헬스 체크
GET /health
```

---

## 9. 리스크 관리

### Kill Switch (비상 정지)

엔진이 자동으로 거래를 중단하는 조건:
- 최대 손실 한도 도달 (기본: 5%)
- 연속 체결 실패 (기본: 5회)
- API 응답 지연 (기본: 5초 초과)

수동 정지:
```bash
curl -X POST http://localhost:8000/api/v1/risk/halt
```

### Circuit Breaker (서킷 브레이커)

거래소별 자동 차단:
- **CLOSED**: 정상 거래
- **OPEN**: 에러 임계값 초과 시 차단 (60초 대기)
- **HALF-OPEN**: 테스트 거래 시도 후 복구 또는 재차단

### Position Limits (포지션 한도)

- 단일 포지션 최대: `MAX_POSITION_SIZE_USDT` (기본: $35)
- 총 노출 한도: `MAX_TOTAL_EXPOSURE_USDT` (기본: $100)
- 거래소별 한도 설정 가능

---

## 10. 문제 해결

### 자주 묻는 질문

**Q: Paper 모드에서 손실이 발생합니다**
A: Paper 모드의 합성 데이터는 GBM 기반이므로 스프레드 주입 빈도(`--injection-rate`)와 크기(`--injection-bps`)를 높여보세요. 또는 `make tune`으로 파라미터를 최적화하세요.

**Q: 테스트가 실패합니다**
A: `pip install -e ".[dev]"`로 의존성을 재설치하고 `make test`를 실행하세요.

**Q: 거래소 연결이 안 됩니다**
A: API 키를 확인하고, 테스트넷을 사용하는 경우 `EXECUTION_MODE=sandbox`를 설정했는지 확인하세요.

**Q: 킬 스위치가 활성화되었습니다**
A: `curl -X POST http://localhost:8000/api/v1/risk/clear`로 해제하거나 엔진을 재시작하세요. 원인을 먼저 파악한 후 해제하세요.

**Q: Docker 서비스가 시작되지 않습니다**
A: `docker compose ps`로 상태를 확인하고, `docker compose logs -f <service>`로 로그를 확인하세요.

### 로그 확인

```bash
# 엔진 로그 (Docker)
docker compose logs -f engine

# 직접 실행 시
EXECUTION_MODE=paper python -m src.main 2>&1 | tee engine.log
```

### 지원

- GitHub Issues: https://github.com/100aniv/leviathan-arbitrage/issues
