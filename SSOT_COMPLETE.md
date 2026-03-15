# LEVIATHAN — SSOT Archive (완료된 Phase + 결정 로그 + RESOLVED 이슈)

> **이 문서는 SSOT.md의 아카이브입니다.** 완료된 Phase(A~M), 결정 로그, RESOLVED 이슈를 보관합니다.
> 활성 설계 문서는 `SSOT.md`를 참조하세요. TF QF/SF/Final 검증 시에만 이 문서를 참조합니다.
> 마지막 이동: 2026-03-15 (토큰 최적화)

---

## 완료된 Phase (A~M) — 전부 ☑ ALL PASS

### Phase A: 인프라 재정비 (US-001~009) — ☑ ALL PASS

- [x] US-001: OMC State 초기화 + project-memory 수정
- [x] US-002: SSOT.md 생성 (기존 문서 통합)
- [x] US-003: 커스텀 에이전트 정의 파일 생성
- [x] US-004: settings.local.json 권한 완화
- [x] US-005: CLAUDE.md 업데이트
- [x] US-006: prd.json 생성
- [x] US-007: notepad.md 생성
- [x] US-008: 기존 문서 아카이브
- [x] US-009: Phase A 통합 검증

### Phase B-1: Foundation (GAP 9,10) — US-010~013 — ☑ ALL PASS

- [x] US-010: okx/bitget futures 수수료 추가 (DEFAULT_FEES + WITHDRAWAL_FEES)
- [x] US-011: Unknown exchange fallback (ValueError → 0.25% + logging)
- [x] US-012: estimate_cost() Protocol 브릿지 (CostCalculator)
- [x] US-013: 7개 전략 통합 테스트 (+47 tests, 3063 total)

### Phase B-2: Futures Infrastructure (GAP 5,6) — US-014~018 ✅ ALL PASS

- [x] US-014: BinanceFuturesCollector 검증 (17 unit tests)
- [x] US-015: CollectorManager futures 등록 + Shadow 분리 검증 (13 unit tests)
- [x] US-016: FundingRateCollector 구현 — 4 거래소 REST (23 unit tests)
- [x] US-017: Engine.run()에 FundingRateCollector 연결 (shadow.py + main.py)
- [x] US-018: Futures + FundingRate 통합 테스트 (19 integration tests)

### Phase B-3: Signal Production (GAP 7,3,2) — US-019~022 ✅

- [x] US-019: TriangularScanner (Bellman-Ford) — 18 unit tests
- [x] US-020: RealDataSignalProducer (실 데이터 신호) — 10 unit tests
- [x] US-021: Shadow mode에 RealDataSignalProducer 연결 — 384줄 인라인 삭제
- [x] US-022: 4종 신호 타입 통합 테스트 — 17 integration tests

### Phase B-4: Shadow Integration (GAP 1) — US-023~026 ✅ ALL PASS

- [x] US-023: ShadowMode에 StrategyManager 주입 + route_signal() (16 tests)
- [x] US-024: 전략별 메트릭 추적 (by_strategy + Prometheus + Telegram breakdown)
- [x] US-025: main.py Shadow mode에 StrategyManager 전달 + start_strategy()
- [x] US-026: Shadow 전략 통합 테스트 (10 integration tests)

### Phase B-5: Multi-Leg Executor (GAP 4) — US-027~029 ✅ ALL PASS

- [x] US-027: ExecutionResult N-leg 확장 (legs:list[LegResult] + compat properties)
- [x] US-028: execute_multi_leg() + 역순 rollback + TradeRequestConsumer 라우팅
- [x] US-029: 3-leg triangular 실행 테스트 (14 unit + 4 integration)

### Phase C: Strategy Validation — US-030~036 ✅ ALL PASS

- [x] US-030: cross_exchange 전략 객체 경유 Shadow 10min (132T, 100%WR, +$34.97)
- [x] US-031: spot_futures (CONDITIONAL: 신호 생성 확인, 비용>basis로 정상 필터)
- [x] US-032: futures_futures (CONDITIONAL: 선물 거래소 1개, 코드 검증 완료)
- [x] US-033: funding_rate (PASS: 4거래소×8심볼 수집, 0 failures)
- [x] US-034: triangular (CONDITIONAL: scanner 검증 완료, 실시장 cycle 미감지)
- [x] US-035: statistical_arb (PASS: z-score 계산, 2 trades 실행)
- [x] US-036: 전체 통합 (PASS: 7 전략 동시, PnL 분리, crash 0)

### Phase D: Dashboard UX — US-037~041 — ☑ ALL PASS (코드 레벨, Chrome 검증은 D-verify)

- [x] US-037: Trade History + Alerts 페이지
- [x] US-038: Settings 페이지 + Logout 기능
- [x] US-039: Strategy Analytics + Funding Rate 모니터
- [x] US-040: Exchange Status 대시보드
- [x] US-041: Mobile Responsive + 전략별 API endpoint

### Phase D-verify: 브라우저 검증 — US-063~064 ☑ ALL PASS

- [x] US-063: 대시보드 Chrome 브라우저 검증 — 핵심 4페이지 (Overview, Trades, Settings, Login)
- [x] US-064: 대시보드 모바일 반응형 + Settings/Alerts 페이지 검증

### Phase E-1: Production Monitoring — US-042~044 — ☑ ALL PASS

- [x] US-042: Telegram 인프라 모니터링 daemon
- [x] US-043: Grafana 대시보드 프리셋
- [x] US-044: 자동 알림 규칙

### Phase E-2: Auto-Tuning Pipeline — US-045~048 — ☑ ALL PASS

- [x] US-045: Scheduled Offline Tuner (Docker)
- [x] US-046: Shadow Runner 자동 적용 + TimescaleDB 데이터
- [x] US-047: Adaptive Threshold + Regime Detector (28 tests, 4 MEDIUM fixes)
- [x] US-048: 3-Layer 튜닝 통합 테스트 (17 integration tests)

### Phase E-3: Production Readiness — US-049~053 — ☑ ALL PASS

- [x] US-049: Capital Allocator (Kelly Criterion, Half-Kelly, 19 tests)
- [x] US-050: Inventory Rebalancer + Balance Tracker (27 tests)
- [x] US-051: Performance Attribution Engine (13 tests)
- [x] US-052: TimescaleDB 자동 백업 + Position Recovery (12 tests)
- [x] US-053: Dashboard Attribution 페이지

### Phase SR: Shadow 현실성 강화 — US-058~062 ☑ ALL PASS

- [x] US-058: PaperExecutor 부분체결(5%) + 주문거부(2%) 활성화 — 12 tests, 3484 total PASS
- [x] US-059: Shadow 레그 간 실행 지연(50-300ms) 추가 — 8 tests, 3492 total PASS
- [x] US-060: BookWalkSlippage — 오더북 깊이별 VWAP 체결 — 15 tests, 3507 total PASS
- [x] US-061: VirtualBalanceTracker + 깊이 기반 주문 크기 제한 (15 tests, 3522 total PASS)
- [x] US-062: 거래소별 Rate Limit 시뮬레이션 (11 tests, 3533 total PASS)

### Phase G: 전략 수익성 복원 — US-066~068

- [x] US-066: Stale Orderbook 감지 + 블랙리스트 + 손실 제한 — StaleOrderbookDetector 4계층 방어, 34 tests, 3609 total PASS
- [x] US-067: 전략별 개별 1H Shadow 검증 — StrategyValidationOrchestrator 구현, STRATEGY_SIGNAL_ID_MAP 기반 격리, 18 tests, 3627 total PASS
- [x] US-068: Shadow 기반 파라미터 재최적화 — Optuna 파이프라인 latency_arb 추가, TimescaleDB/activation 필터 연동, param_bridge 키 정규화, 13 tests, 3640 total PASS

### Phase H: 대시보드/프론트 통합 완성 — US-065, US-069~072 ☑ ALL PASS

- [x] US-065: Shadow→Dashboard 데이터 브리지 — ShadowMode.get_snapshot() + /api/v1/shadow/stats REST + ShadowPanel 컴포넌트 + WS feed shadow_stats 통합. 3,656 tests PASS
- [x] US-069: Overview 종합 상황판 리디자인 — PortfolioSummary(4 KPI + 거래소 상태바) + RiskGauge(MDD 게이지) + PerformanceTrend(PnL 추세) + EventFeed(실시간 피드). 81 tests PASS
- [x] US-070: Attribution/Funding/System 빈 페이지 완성 — 3개 페이지 실 컨텐츠 구현 (전략별 수익 분석, 펀딩레이트 추적, 인프라 상태). 81 tests PASS
- [x] US-071: GlobalHeatmap + OrderbookView 실 데이터 연결 — REST polling fallback 구현, 거래소 선택 기능. 81 tests PASS
- [x] US-072: 계좌 정보/총자산/거래소별 잔고 표시 — GET /api/v1/portfolio-summary + VirtualBalanceTracker 기반 거래소별 잔고 + PortfolioSummary.tsx 컴포넌트. 12 tests, 3,668 total PASS

### Phase I: 거래소/전략 완성도 — US-073~076

- [x] US-073: Bithumb REST 스냅샷 → 증분 orderbook 근본 해결 (누적 book, stale 5초 감지, parallel re-sync)
- [x] US-074: Coinone WS 안정성 강화 (지터 백오프, watchdog 120초, app PING 25분, symbol stale 감지)
- [x] US-075: futures_futures 전략 활성화 (OKX/Bybit futures 수집기, DEFAULT_EXCHANGES 8→10)
- [x] US-076: 전략/거래소 완성도 전수 감사

### Phase J-EXT: 보안+UX+엔진+인프라 강화 (6-관점 GAP 분석) — US-105~122

> 6개 관점(UX, 퀀트, DevOps, PM, 보안, 경쟁분석) 통합 GAP 분석 결과.
> 상세: `.claude/plans/modular-seeking-wreath.md`

**Wave 1 — 보안 (간단, 먼저)**
- [x] US-105: JWT 시크릿 기본값 제거 + bcrypt 비밀번호 해싱 (기본값 fallback 제거 → 미설정 시 서버 거부, 평문→bcrypt) ✅
- [x] US-106: WebSocket 피드 JWT 인증 (WS 핸드셰이크 시 토큰 검증) ✅

**Wave 2 — 대시보드 UX**
- [x] US-107: 모드 전환 UI 연결 + 친화적 명칭 (ModeSwitch.tsx 신규, PATCH /api/v1/settings/mode, shadow→"시뮬레이션"/paper→"연습"/live→"실거래", Live 전환 시 LiveGate 확인 다이얼로그) ✅
- [x] US-108: 포트폴리오 별도 탭 (portfolio/page.tsx + EquityCurve.tsx 신규, GET /portfolio/equity-curve + /portfolio/metrics, Sharpe/MDD/Calmar 리스크 메트릭스, 자산배분 바 차트) ✅
- [x] US-109: 오버뷰 개선 (ROI%, 시스템 성능 위젯, "Shadow Monitor"→현재 모드명 동적 변경) ✅
- [x] US-110: 히트맵 심볼 확장 (GlobalHeatmap.tsx Major 8/Top 20/All/Custom 드롭다운, All 시 엔진 전체 심볼 표시, Custom 드롭다운 로컬 저장) ✅
- [x] US-111: 거래 설명 기능 ("왜 이 거래를?" — GET /trades/{id} + TradeDetail 사이드 패널, reason/spread_bps/fee_usd/net_pnl)
- [x] US-112: 트레이드 필터링 + CSV 내보내기 (날짜/전략/거래소/심볼 필터 + RFC 4180 CSV 다운로드)
- [x] US-113: 용어 친화화 + 툴팁 ("War Room"→"대시보드", "MIN_EDGE_BPS"→"최소 수익 기준" + info 아이콘) ✅

**Wave 3 — 엔진 강화**
- [x] US-114: 동적 포지션 사이징 (신뢰도(edge) × 레짐(RegimeDetector) × 유동성(DepthAnalyzer) 기반, CRISIS 25%, LOW vol 150%) ✅
- [x] US-115: 슬리피지 피드백 루프 (실제 체결가 vs 예상가 비교 → EMA로 모델 파라미터 자동 조정) ✅
- [x] US-116: TCA 모듈 + 실행 레이턴시 위젯 (TCAAnalyzer + PercentileTracker, GET /api/v1/tca/summary JWT, TCAWidget.tsx System 탭) ✅
- [x] US-117: 텔레그램 양방향 명령어 (/status, /kill, /mode, /balance — 단일 봇으로 통합) ✅
- [x] US-118: 전략 간 상관관계 모니터링 (30-trade 롤링 상관계수, >0.7 시 소규모 전략 50% 축소) ✅
- [x] US-119: IOC 주문 타입 (IOC 리밋 우선 → 타임아웃 시 마켓 폴백) ✅
- [x] US-120: 인벤토리 리밸런싱 통합 확인 (main.py wiring + _rebalancer_loop 4h 주기 + Telegram CRITICAL/WARNING 알람) ✅

### Phase K: Regime Detection 기반 구축 — US-081~085

- [x] US-081: ML 의존성 + HMM 3-regime 설계 (hmmlearn/sklearn [ml] dep, MarketRegime CALM/NORMAL/VOLATILE 확장, HMMRegimeDetector 클래스) ✅
- [x] US-082: 레짐 피처 엔지니어링 (RegimeFeaturePipeline 10-feature: vol×3, spread×2, volume×2, momentum×2, order_flow×1 + normalize + fill_missing) ✅
- [x] US-083: HMM 학습 파이프라인 (HMMTrainer: fetch→extract→fit→캐시, 주간 배치 스케줄러, predict <2ms) ✅
- [x] US-084: 레짐→시그널 통합 (REGIME_MIN_EDGE: CALM:3bps, NORMAL:5bps, VOLATILE:8bps, CRISIS:15bps + SignalGenerator regime_detector 파라미터) ✅
- [x] US-085: Walk-forward 레짐 검증 (RegimeWalkForwardAnalyzer: 레짐-성과 상관분석, regime-adaptive vs fixed PnL 비교, walk-forward PASS 검증) ✅

### Phase L: DEX 실시간 + 가스비 통합 — US-086~090

- [x] US-086: 실시간 가스비 오라클 (GasOracle: 6 chains, 30초 캐시, RPC→fallback, [dex] optional dep) ✅
- [x] US-087: CostCalculator DEX 확장 (LP fee + gas + MEV 추정 + bridge cost) ✅
- [x] US-088: Uniswap V3 실시간 가격/슬리피지 (slot0 → 가격, liquidity → VWAP) ✅
- [x] US-089: CEX-DEX 스프레드 스캐너 (net spread 가스비 차감 후) ✅
- [x] US-090: CEX-DEX Shadow 검증 ✅

### Phase M: 로컬 ML 시그널 파이프라인 — US-091~096

- [x] US-091: ML 피처 파이프라인 (orderbook/volatility/volume/regime/execution) ✅
- [x] US-092: XGBoost 학습 루프 (주간 배치, optuna HPO) ✅
- [x] US-093: ONNX 내보내기 + 버전관리 (onnxmltools, opset 관리) ✅
- [x] US-094: ONNX Runtime 추론 통합 (<1ms 보장, SignalGenerator 연동) ✅
- [x] US-095: ML 시그널 백테스트 (walk-forward A/B 비교) ✅
- [x] US-096: Production Canary (Paper→Shadow ML 시그널 검증) ✅

### Phase J-EXT Wave 4 — 인프라 (K/L/M 완료 후 실행) — US-121~122

- [x] US-121: Loki + Promtail 로그 집계 (Grafana 연동, 크로스 컨테이너 검색) ✅
- [x] US-122: WAL 백업 + PITR (RPO <1시간, 주간 복원 검증) ✅

---

## 결정 로그

| 날짜 | 결정 | 근거 |
|------|------|------|
| Phase 4 | ccxt 미사용, 네이티브 어댑터 | ccxt 레이턴시 오버헤드, 커스텀 최적화 불가 |
| Phase 8 | Testnet 단계 제거 | 거래소별 testnet 불안정, Shadow가 대체 |
| 7.3b | max_spread_pct=5.0 게이트 | Bithumb 허위 스프레드 60%+ 방지 |
| 7.3b | 배치 WS 구독 훅 추가 | Upbit/Bithumb 단일 구독 메시지 요구 |
| 7.3d | KRW dual-source 실시간 환율 | 정적 1380 vs 실제 1477 괴리 해소 |
| 7.3d | MIN_PRICE_USD=0.10 | 소액 코인 슬리피지 리스크 감소 |
| 7.3h | MIN_EDGE_BPS=5 확정 | 40=없음, 30=거의없음, 5=모든 시간대 수익 |
| 7.3j | PaperExecutor ZERO slippage | 이중 슬리피지 계산 방지 (CEXOrderbook이 유일 소스) |
| 7.3k | transfer_coin 동적 할당 | network cost: BTC=$1.39, ETH=$0.06~$4.50 (거래소별, L2 Arbitrum 우선), XRP=$0.40 등 |
| SR | Docker Shadow 필수화 | Shadow 테스트 중 Docker 미실행 발견. graceful degradation으로 거래 로직 유효하나 TimescaleDB/Redis 미저장. 향후 Shadow 실행 전 `docker compose up -d` 필수 |
| SR | Phase D 브라우저 테스트 필수화 | US-037~041, US-053 대시보드 US가 `npm run build`만으로 passes:true 처리됨. 실제 Chrome 렌더링/API 연동/WebSocket 피드 검증 미완. Phase D 완료 기준에 Chrome 브라우저 테스트 추가 |
| SR | Shadow 현실성 6개 GAP 식별 (SG-1~SG-6) | PaperExecutor가 데모급(100% fill, 0ms delay, 무한잔고). 상용급 전환 위해 부분체결/지연/깊이VWAP/가상잔고/Rate Limit 추가 필요 |
| Phase G | 4계층 stale 방어 (cross-validation + periodic refresh + update_count gate + loss cap) | 1H Shadow -$1,937 fat-tail 방지, defense-in-depth |
| Phase G | 단일 ShadowMode 인스턴스 재사용 + 동적 _disabled_strategies 전환 | WS 재연결 비용 절감 (7×30s 절약), VirtualBalanceTracker/RateLimiter/StaleDetector reset으로 전략 간 격리 보장 |
| Phase G | latency_arb Optuna 튜닝 파이프라인 추가 | US-068: statistical_arb/cex_dex 제외, activation filter 결과 연동, TimescaleDB 데이터 기반 최적화 |
| Phase G | param_bridge 키 정규화 (max_position_size_usdt) | strategy_params.json의 '최대_포지션_크기_usdt'를 'max_position_size_usdt'로 정규화하여 Optuna 반환값과 일치 |
| Phase H US-065 | ShadowMode.get_snapshot() 공개 메서드 + EngineContext.shadow_mode 필드 | Shadow 메트릭을 REST/WS 양방향으로 대시보드에 노출. shadow_router 별도 마운트로 관심사 분리. ShadowPanel 조건부 렌더링으로 Shadow 모드 비활성 시 UI 숨김 |
| Phase H US-069 | 4개 신규 컴포넌트 + Overview 페이지 리디자인 | PortfolioSummary(상태 배지 + 4 KPI), RiskGauge(SVG 게이지), PerformanceTrend(선 그래프), EventFeed(피드). grid 반응형 레이아웃 (1-col mobile, 2-col xl+). useEngineWs() + useApi() hook으로 실시간 데이터 연동 |
| Phase H US-070 | Attribution/Funding/System 페이지 실 컨텐츠 구현 | 3개 빈 페이지를 함수형 컴포넌트로 변환. REST API 연동 (getAttributionData, getFundingMetrics, getSystemHealth). 테스트 통합 (81 total) |
| Phase H US-071 | GlobalHeatmap + OrderbookView REST polling fallback | API 장애 시 mock 데이터로 fallback. 거래소 드롭다운 선택기 추가. 재시도 로직 (exponential backoff) |
| Phase H US-072 | VirtualBalanceTracker 기반 포트폴리오 요약 API | /api/v1/portfolio-summary 신설. Shadow 모드에서 VirtualBalanceTracker 직접 조회, 비Shadow 시 exchange_status fallback. PortfolioSummary.tsx로 거래소별 잔고 breakdown + mode badge 표시 |
| Phase I US-073 | Bithumb 누적 orderbook 방식 채택 | REST 스냅샷 후 증분 적용(full_snapshot→updates). 허위 스프레드 근본 해결. stale 5초 감지 + parallel re-sync로 데이터 신뢰성 확보 |
| Phase I US-074 | 지터 백오프 공통화 + Coinone watchdog | 재연결 지터(jitter backoff) 패턴 공통화. Coinone watchdog 120초, app PING 25분으로 장시간 연결 안정성 확보. symbol stale 감지 추가 |
| Phase I US-075 | OKX/Bybit futures -SWAP 접미사 + DEFAULT_EXCHANGES 8→10 | okx_futures/bybit_futures 수집기 신설. 심볼 형식: BTC-USDT-SWAP (OKX), BTCUSDT (Bybit). futures_futures 전략 활성화 조건(2+ 선물 거래소) 충족 |
| Phase J-EXT US-106 | WS JWT: query param ?token= 우선 + cookie leviathan_token fallback | 브라우저 WebSocket API가 커스텀 헤더 미지원. 업계 표준(Socket.IO, Slack). accept()→close(4003) 패턴으로 미인증 연결 즉시 거부 |
| Phase J-EXT US-107 | ModeSwitch: PATCH /api/v1/settings/mode + LiveGate 확인 다이얼로그 | Live 전환 시 6-check LiveGate 통과 여부 사전 확인. 한글 명칭(시뮬레이션/연습/실거래)으로 비개발자 친화적 UX 개선 |
| Phase J-EXT US-108 | 포트폴리오 탭 신설: equity-curve + metrics 별도 API | Overview 과부하 방지. EquityCurve.tsx + 자산배분 바 차트로 수익성 가시화. Sharpe/MDD/Calmar 3종 리스크 지표 통합 |
| Phase J-EXT US-110 | GlobalHeatmap 심볼 필터: Major 8/Top 20/All/Custom + 로컬 저장 | All 모드에서 엔진 전체 175심볼 렌더링. Custom 설정은 localStorage 저장으로 세션 유지 |
| Phase J-EXT W3-B1 | Telegram fail-closed auth: 빈 allowed_chat_ids → 전부 차단 | TelegramCommandHandler 요청 시 미설정 상황 fail-closed 원칙 적용. 인가된 chat_ids 없으면 명령어 거부 |
| Phase J-EXT W3-B1 | CorrelationMonitor → Guardian check() #9 통합 | 5개 모듈(DynamicSizer, SlippageFeedbackLoop, CorrelationMonitor, TelegramCommandHandler, AtomicOrderExecutor) main.py wiring 필수화. CorrelationMonitor 결과는 Guardian check #9로 로그만 기록, DynamicSizer가 실제 포지션 축소 담당 |

---

## 9. RESOLVED 알려진 이슈

### ~~CRITICAL — Architecture GAPs (RESOLVED 7/10건)~~

| GAP | 설명 | 해결 Phase |
|-----|------|-----------|
| ~~**1**~~ | ~~Shadow가 Strategy 객체 우회 — StrategyManager.route_signal() 도입~~ | ~~B-4~~ |
| ~~**2**~~ | ~~SignalGenerator가 cross_exchange 신호만 생산 — RealDataSignalProducer 도입~~ | ~~B-3~~ |
| ~~**4**~~ | ~~AtomicExecutor 2-Leg만 지원 — execute_multi_leg() N-leg 도입~~ | ~~B-5~~ |
| ~~**5**~~ | ~~Futures 데이터 파이프라인 부재~~ | ~~B-2~~ |
| ~~**6**~~ | ~~Funding Rate Collector 부재~~ | ~~B-2~~ |
| ~~**9**~~ | ~~Fee Model에 okx/bitget futures 누락 + ValueError~~ | ~~B-1~~ |
| ~~**10**~~ | ~~CostCalculator Protocol 불일치 (estimate_cost 없음)~~ | ~~B-1~~ |

### ~~CRITICAL — Shadow Realism GAPs (RESOLVED 6/6건, Phase SR)~~

| GAP | 설명 | 해결 US |
|-----|------|---------|
| ~~**SG-1**~~ | ~~partial_fill_rate=0.0, rejection_rate=0.0 → 0.05, 0.02 활성~~ | ~~US-058~~ |
| ~~**SG-2**~~ | ~~레그 간 0ms 동기 실행 → 50-300ms 랜덤 지연 활성~~ | ~~US-059~~ |
| ~~**SG-3**~~ | ~~PowerLawSlippage 오더북 깊이 미반영 → BookWalkSlippage VWAP 활성~~ | ~~US-060~~ |
| ~~**SG-4**~~ | ~~무한 가상 잔고 → VirtualBalanceTracker 활성 ($10M/exchange)~~ | ~~US-061~~ |
| ~~**SG-5**~~ | ~~trade_size=1 하드코딩 → compute_depth_trade_size (L1×0.10)~~ | ~~US-061~~ |
| ~~**SG-6**~~ | ~~Rate limit 시뮬레이션 없음 → ShadowRateLimiter 토큰 버킷 활성~~ | ~~US-062~~ |

### RESOLVED 기타 이슈

| 이슈 | 해결 |
|------|------|
| GAP 5: Futures 데이터 파이프라인 | BinanceFuturesCollector 검증 + Shadow futures_books 분리 (Phase B-2) |
| GAP 6: Funding Rate Collector | 4 거래소 REST collector + Engine wiring (Phase B-2) |
| MIN_EDGE_BPS 최적화 | 5bps 확정 (Phase 7.3h) |
| _krw_rate=0 ZeroDivisionError | fallback 1380 가드 추가 (Phase 7.3f) |
| KRW/USDT 정적 환율 | dual-source 동적 조회 구현 (Phase 7.3d) |
| 이중 슬리피지 | PaperExecutor ZERO slippage 적용 (Phase 7.3j) |
| PowerLawSlippage k/gamma 무시 | 실제 공식 적용 완료 (Phase 3.5) |
| Stale Orderbook fat-tail loss | StaleOrderbookDetector 4계층 방어 + loss cap $50 (Phase G US-066) |
| Bithumb 증분 Orderbook | 누적 orderbook + REST re-sync + stale 5초 감지 (Phase I US-073) |
| Shadow 손실 전략 미비활성화 | SHADOW_DISABLED_STRATEGIES .env 설정 — stat_arb/spot_futures/latency_arb 비활성 (Phase S5 US-156) |
| 전략 6개 비활성 | Phase J-EXT Wave 1~4에서 전략 연결 완료 |
