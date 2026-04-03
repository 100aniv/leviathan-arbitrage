# Phase K 잔여 작업 PLAN.md (2026-04-03)

## 대상 US (6개)
- US-372: Paper P-01~P-23 (2H~4H each, crash=0, trade>=1)
- US-332: Paper 24H 무중단 (US-372 누적 24H → 자동 충족)
- US-382: Paper P-24~P-31 (Bybit/OKX/MEXC/Gate.io/BingX WS, 4H each)
- US-373: 전체 병렬 24H (4조합 동시 실행)
- US-055: LiveGate Preflight 10항목
- US-056: 첫 실거래 (사장님 DevBot /approve)

## 의존 체인
US-372 → US-332 → US-373 → US-055 → US-056
US-382 (US-372와 병렬 가능)

## Stage B 실행 계획

### B-Step 1: 인프라 준비
1. Docker: timescaledb + redis 상시 가동 (이미 running)
2. engine container stop (로컬 실행과 충돌 방지)
3. 엔진 로컬 실행: `cd engine && python -m src.main` (Shadow mode)

### B-Step 2: Shadow 검증 (10분+)
- Shadow 10분 실행 → 13항목 복합지표 확인
- PnL>0, crash=0, 전략별 trade>=1

### B-Step 3: Paper 테스트 (US-372 + US-382)
- engine 실행 상태에서 API `/api/paper/start` 호출
- run_paper_tests.py --all-basic (P-01~P-23): 2H~4H/case
- run_paper_tests.py --all-extended (P-24~P-31): 4H/case
- 누적 시간 24H 도달 → US-332 자동 충족

### B-Step 4: LiveGate Preflight (US-055)
10항목 순서대로:
1. DB health (TimescaleDB)
2. WS connections (거래소별 ping)
3. API키 검증 (Binance/Upbit/Bithumb/Coinone)
4. 잔고 확인 (spot: $20, futures: $30)
5. KillSwitch test
6. Circuit Breaker test
7. LiveGate config (engine.json mode=live)
8. Telegram 3-Bot 연결
9. Adapter health (11개 WS)
10. Paper 72H 데이터 확인 (US-332 PASS 필요)

### B-Step 5: 첫 실거래 (US-056)
- DevBot /approve 후 live 진입
- filled_qty>0, MDD<3%
- 사장님 직접 감시

## 완료 기준
- Shadow 13항목 PASS
- Paper P-01~P-23 각 case: crash=0, trade>=1
- Paper 24H 누적 달성
- LiveGate 10항목 PASS
