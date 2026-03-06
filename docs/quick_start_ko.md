# LEVIATHAN 빠른 시작 가이드

> 5분 안에 페이퍼 트레이딩을 시작하세요. API 키가 필요 없습니다.

## 1. 설치

```bash
# 저장소 클론
git clone https://github.com/100aniv/leviathan-arbitrage.git
cd leviathan-arbitrage

# 의존성 설치
cd engine
pip install -e ".[dev]"
```

## 2. 백테스트 실행 (30초)

합성 데이터로 전략 성능을 즉시 확인할 수 있습니다.

```bash
# 기본 백테스트 (합성 데이터 2000개 캔들)
python -m src.cli.backtest_cli --data synthetic

# 또는 Makefile 사용
make backtest
```

출력 예시:
```
Backtest (synthetic)
  Total PnL:      $72.23
  Sharpe Ratio:   0.45
  Win Rate:       17.2%
  Num Trades:     64
```

## 3. 페이퍼 트레이딩 실행 (1분)

실시간 합성 오더북으로 전체 파이프라인을 테스트합니다.

```bash
# 1분 페이퍼 트레이딩 (verbose 모드)
python -m src.cli.paper_runner --duration 60 --verbose

# 또는 Makefile 사용
make paper-trade-quick
```

출력 예시:
```
Paper trading started (duration: 60s)
  Exchanges: paper_binance, paper_upbit
  Capital: $70.00 per exchange

LEVIATHAN Performance Report
  Total PnL:       $4.49
  Sharpe Ratio:    7.33
  Win Rate:        61.9%
  Profit Factor:   5.28
  Beta Gate:       [PASS]
```

## 4. 자동 튜닝 (2분)

Walk-forward 최적화로 최적 파라미터를 찾습니다.

```bash
# 빠른 튜닝 (20 trials)
python -m src.cli.tune_cli --data synthetic --trials 20

# 섀도우 평가 포함
python -m src.cli.tune_cli --data synthetic --trials 50 --shadow

# 또는 Makefile 사용
make tune-quick
```

## 5. 다음 단계

| 단계 | 명령어 | 설명 |
|------|--------|------|
| 5분 페이퍼 트레이딩 | `make paper-trade` | 더 긴 시간 실행 |
| 정밀 튜닝 | `make tune` | 50 trials + shadow 평가 |
| CSV 데이터 백테스트 | `python -m src.cli.backtest_cli --data ./data.csv` | 실제 데이터 사용 |
| 샌드박스 모드 | `EXECUTION_MODE=sandbox python -m src.main` | 테스트넷 연결 |
| 라이브 모드 | `EXECUTION_MODE=live python -m src.main` | 실전 거래 |

자세한 내용은 [종합 사용자 가이드](user_guide_ko.md)를 참고하세요.
