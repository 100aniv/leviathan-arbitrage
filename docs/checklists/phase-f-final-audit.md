# Phase F 종합 검사지 (Final Audit Checklist)

> Phase F는 모든 기능이 구현·검증된 후 진입하는 **마지막 관문**.
> 전 항목 PASS 필수. 하나라도 FAIL 시 Live 전환(US-056) 절대 금지.

## 검사지 실행 분담

| 팀 | 담당 카테고리 |
|----|-------------|
| 기획팀(AESPA) | 9. 모니터링/알림, 10. 운영 준비도 |
| 개발팀(BLACKPINK) | 1. 엔진 코어, 7. 대시보드 UI/UX |
| 퀀트팀 | 2. 전략, 5. 실행 시뮬레이션 |
| 테스트팀 | 3. 거래소, 6. 성능, 8. 인프라 |
| 검증팀 | 4. 리스크 관리, 최종 크로스체크 |

---

## 1. 엔진 코어 (15항목) — 개발팀

- [ ] 1.1 엔진 시작 정상 (python -m src.main → no crash)
- [ ] 1.2 Graceful shutdown (SIGTERM → 정상 종료, 포지션 저장)
- [ ] 1.3 EventBus 메시지 전달 확인 (publish→subscribe 동작)
- [ ] 1.4 KillSwitch 3-tier 동작 (WARN→HALT→EMERGENCY)
- [ ] 1.5 CircuitBreaker CLOSED→OPEN→HALF_OPEN 전환
- [ ] 1.6 RiskGuardian 9-check 전부 작동
- [ ] 1.7 StrategyManager 7개 전략 등록/시작 확인
- [ ] 1.8 CollectorManager 8개 거래소 등록 확인
- [ ] 1.9 PaperExecutor 정상 동작 (Shadow 모드)
- [ ] 1.10 MultiStrategySignalProducer 신호 생성 확인
- [ ] 1.11 CostCalculator estimate_cost() 호출 정상
- [ ] 1.12 ExecutionResult N-leg 확장 동작
- [ ] 1.13 CapitalAllocator Kelly Criterion 계산 정상
- [ ] 1.14 PositionRecovery 비정상 종료 후 복구 동작
- [ ] 1.15 BalanceTracker 거래소별 잔고 폴링 정상

## 2. 전략 (8개 x 5항목 = 40항목) — 퀀트팀

### 2.1 cross_exchange
- [ ] 신호 생성 → 거래 실행 → PnL 기록
- [ ] 최소 엣지 필터링 (MIN_EDGE_BPS) 동작
- [ ] 비용 계산 정확도 (fee + slippage + network)
- [ ] 거래소 간 가격 차이 감지 정상
- [ ] 1H Shadow PnL > 0

### 2.2 spot_futures
- [ ] Basis 계산 정확도 (spot vs futures 가격 차이)
- [ ] 동일 거래소 내 spot/futures 매칭
- [ ] Stale orderbook 감지 작동 (Phase G 적용 후)
- [ ] 비용 > basis 시 정상 필터링
- [ ] 1H Shadow crash = 0

### 2.3 futures_futures
- [ ] 선물 거래소 간 spread 감지
- [ ] 최소 2개 futures 거래소 연결 확인
- [ ] N-leg 실행 정상
- [ ] Rollback 로직 동작
- [ ] 1H Shadow crash = 0

### 2.4 triangular
- [ ] Bellman-Ford negative cycle detection 정상
- [ ] 3-leg 순차 실행 동작
- [ ] Depth-aware net profit 계산 정확
- [ ] Rollback (2-leg 성공 + 3-leg 실패) 동작
- [ ] 1H Shadow crash = 0

### 2.5 funding_rate
- [ ] 4개 거래소 funding rate 수집 정상
- [ ] Rate diff > threshold 시 신호 발생
- [ ] 60초 간격 polling 동작
- [ ] 비용 계산에 funding cost 포함
- [ ] 1H Shadow crash = 0

### 2.6 statistical_arb
- [ ] Kalman filter z-score 계산 정상
- [ ] Cointegration test 실행 확인
- [ ] 진입/이탈 신호 생성
- [ ] Exit TradeRequest 정상 생성
- [ ] 1H Shadow crash = 0

### 2.7 latency_arb
- [ ] 거래소 간 latency 차이 감지
- [ ] Stale data 필터링 동작 (Phase G 적용 후)
- [ ] 최소 edge 필터링 동작
- [ ] 비용 계산 정확
- [ ] 1H Shadow crash = 0

### 2.8 cex_dex (CONDITIONAL)
- [ ] DEX_RPC_URL 설정 시 활성화 확인
- [ ] DEX 가격 피드 수신 정상
- [ ] CEX-DEX 가격 차이 감지
- [ ] Gas cost 계산 포함
- [ ] 1H Shadow crash = 0 (or N/A if DEX_RPC_URL unset)

## 3. 거래소 (8개 x 5항목 = 40항목) — 테스트팀

### 3.1 Binance (Spot)
- [ ] WebSocket 연결 정상
- [ ] Orderbook 수신 + 파싱 정상
- [ ] 심볼 매핑 정확 (BTC/USDT → btcusdt)
- [ ] 데이터 신선도 < 5초
- [ ] 재연결 로직 동작

### 3.2 Binance Futures
- [ ] wss://fstream.binance.com/ws 연결 정상
- [ ] depthUpdate 메시지 파싱 정상
- [ ] Futures 전용 심볼 매핑
- [ ] Funding rate 수집 연동
- [ ] 재연결 로직 동작

### 3.3 Bybit
- [ ] WebSocket 연결 정상
- [ ] Orderbook 수신 + 파싱 정상
- [ ] 심볼 매핑 정확
- [ ] 데이터 신선도 < 5초
- [ ] 재연결 로직 동작

### 3.4 OKX
- [ ] WebSocket 연결 정상
- [ ] Orderbook 수신 + 파싱 정상
- [ ] 심볼 매핑 정확
- [ ] 데이터 신선도 < 5초
- [ ] 재연결 로직 동작

### 3.5 Bitget
- [ ] WebSocket 연결 정상
- [ ] Orderbook 수신 + 파싱 정상
- [ ] 심볼 매핑 정확
- [ ] 데이터 신선도 < 5초
- [ ] 재연결 로직 동작

### 3.6 Upbit
- [ ] WebSocket 연결 정상
- [ ] KRW 페어 자동 매핑 동작
- [ ] KRW→USDT 환율 변환 정확
- [ ] 데이터 신선도 < 5초
- [ ] 재연결 로직 동작

### 3.7 Bithumb
- [ ] REST 초기 스냅샷 취득 정상
- [ ] 증분 orderbook 업데이트 정상
- [ ] KRW→USDT 환율 변환 정확
- [ ] Stale data 감지 동작 (Phase G/I 적용 후)
- [ ] 재연결 로직 동작

### 3.8 Coinone
- [ ] WebSocket 연결 정상 (or REST fallback)
- [ ] KRW 페어 자동 매핑 동작
- [ ] 0.02% 수수료 적용 확인
- [ ] 데이터 신선도 < 5초
- [ ] 재연결 로직 동작

## 4. 리스크 관리 (10항목) — 검증팀

- [ ] 4.1 Daily loss limit 작동 ($100 이상 손실 시 중단)
- [ ] 4.2 Max drawdown 경고 (MDD > 3% → 경고)
- [ ] 4.3 Max drawdown 중단 (MDD > 5% → KillSwitch)
- [ ] 4.4 Single trade max loss 제한 동작
- [ ] 4.5 Position size limit 동작
- [ ] 4.6 Correlation risk 모니터링
- [ ] 4.7 Exchange exposure limit (단일 거래소 집중 방지)
- [ ] 4.8 Rate limit 준수 (거래소별 토큰 버킷)
- [ ] 4.9 Emergency shutdown 수동 트리거 동작
- [ ] 4.10 Risk dashboard 데이터 정확도

## 5. 실행 시뮬레이션 (10항목) — 퀀트팀

- [ ] 5.1 BookWalkSlippage VWAP 정확도
- [ ] 5.2 VirtualBalanceTracker 잔고 추적 정상
- [ ] 5.3 Rate Limit 토큰 버킷 동작
- [ ] 5.4 Partial fill (5%) 시뮬레이션 동작
- [ ] 5.5 Order rejection (2%) 시뮬레이션 동작
- [ ] 5.6 Inter-leg delay (50-300ms) 적용 확인
- [ ] 5.7 CEXOrderbookSlippage 필터링 정상
- [ ] 5.8 PowerLaw k=0.0 (비활성) 확인
- [ ] 5.9 Fee model 거래소별 정확도
- [ ] 5.10 Network cost 동적 계산 정상

## 6. 성능 (5항목) — 테스트팀

- [ ] 6.1 메모리 누수 없음 (12H 기준 RSS 증가 < 100MB)
- [ ] 6.2 CPU 사용률 안정 (< 80% sustained)
- [ ] 6.3 WS 메시지 처리 지연 < 100ms (p99)
- [ ] 6.4 DB 쿼리 응답 < 500ms (p99)
- [ ] 6.5 API 엔드포인트 응답 < 200ms (p99)

## 7. 대시보드 UI/UX (20항목) — 개발팀

- [ ] 7.1 Overview: 종합 상황판 정보 완전성
- [ ] 7.2 Overview: 시스템 상태 뱃지 (RUNNING/STOPPED/ERROR)
- [ ] 7.3 Overview: 핵심 KPI 카드 (총자산, PnL, 포지션)
- [ ] 7.4 Trades: 실시간 거래 이력 표시 + 필터링
- [ ] 7.5 Strategies: 전략별 상태/메트릭 표시
- [ ] 7.6 Exchanges: 거래소 연결 상태/지연/잔고
- [ ] 7.7 Analytics: 전략별 PnL 누적/WR 추이
- [ ] 7.8 Funding: 거래소별 funding rate 비교
- [ ] 7.9 Attribution: 전략/거래소/페어별 수익 귀속
- [ ] 7.10 Settings: 전략 활성/비활성 토글 동작
- [ ] 7.11 Alerts: 알림 이력 표시
- [ ] 7.12 System: 시스템 정보 페이지 (Phase H에서 완성)
- [ ] 7.13 JWT 로그인/로그아웃 정상
- [ ] 7.14 WebSocket 실시간 업데이트 동작
- [ ] 7.15 모바일 반응형 (375x812) 전 페이지
- [ ] 7.16 태블릿 반응형 (768x1024) 전 페이지
- [ ] 7.17 다크 테마 렌더링 정상
- [ ] 7.18 빈 상태(empty state) 표시 정상
- [ ] 7.19 에러 상태(error state) 표시 정상
- [ ] 7.20 npm run build 0 errors

## 8. 인프라 (10항목) — 테스트팀

- [ ] 8.1 Docker 8 컨테이너 전부 healthy
- [ ] 8.2 TimescaleDB 데이터 저장/조회 확인
- [ ] 8.3 Redis 캐시/WAL 동작 확인
- [ ] 8.4 Prometheus 메트릭 수집 정상
- [ ] 8.5 Grafana 대시보드 18개 메트릭 표시
- [ ] 8.6 Nginx TLS reverse proxy 동작
- [ ] 8.7 Nginx rate limiting 동작
- [ ] 8.8 DB 백업 스크립트 동작 (backup_db.sh)
- [ ] 8.9 Docker compose up → 전 서비스 30초 이내 시작
- [ ] 8.10 Docker compose down → graceful 종료

## 9. 모니터링/알림 (5항목) — 기획팀

- [ ] 9.1 Telegram 알림 전송 정상
- [ ] 9.2 일일 PnL 요약 자동 전송 (UTC 0시)
- [ ] 9.3 PnL 급락 알림 (-$10 이상)
- [ ] 9.4 WS 끊김 알림 (3개 이상 동시)
- [ ] 9.5 Slippage 과다 알림 (> 50bps)

## 10. 운영 준비도 (5항목) — 기획팀

- [ ] 10.1 Runbook 6개 최신화 (kill switch, exchange outage, drawdown, DB recovery, deployment, security)
- [ ] 10.2 장애 대응 매뉴얼 검증 (시뮬레이션 1회)
- [ ] 10.3 백업/복구 테스트 통과
- [ ] 10.4 SSOT.md 최종 동기화 (모든 섹션 최신)
- [ ] 10.5 운영 가이드 문서 완성 (시작/중지/모니터링)

---

## 총계

| 카테고리 | 항목 수 |
|----------|--------|
| 1. 엔진 코어 | 15 |
| 2. 전략 | 40 |
| 3. 거래소 | 40 |
| 4. 리스크 관리 | 10 |
| 5. 실행 시뮬레이션 | 10 |
| 6. 성능 | 5 |
| 7. 대시보드 UI/UX | 20 |
| 8. 인프라 | 10 |
| 9. 모니터링/알림 | 5 |
| 10. 운영 준비도 | 5 |
| **합계** | **160** |

> PASS 기준: 160/160 (100%) 필수. FAIL 항목이 있으면 해당 팀이 수정 후 재검사.
