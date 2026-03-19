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

### RESOLVED 이슈 (S8/S9 해소 — US-193에서 이관)

| 이슈 | 해소 Phase |
|------|-----------|
| ~~GAP 3: MultiStrategy LIVE 미연결~~ | S8 US-169 |
| ~~GAP 7: Triangular Scanner 부재~~ | S8 US-170 |
| ~~GAP 8: DEX Adapter Stub~~ | S8 US-177 |
| ~~ONNX ML Scorer 미연결~~ | S8 US-172 |
| ~~HMM RegimeDetector 미연결~~ | S8 US-173 |
| ~~AdaptiveThreshold 미연결~~ | S8 US-174 |
| ~~ExposureTracker 미인스턴스화~~ | S8 US-175 |
| ~~CorrelationMonitor 로그만~~ | S8 US-176 |
| ~~Docker pre-flight 체크~~ | S8 설정/환경 갭 |
| ~~IOC limit order 미구현~~ | S8 US-178 |
| ~~마찰 vs Gross Spread~~ | S8 US-174 |

### RESOLVED 이슈 (S13/S14 해소 — 2026-03-19 이관)

| 이슈 | 해소 Phase/US |
|------|-------------|
| ~~전략 영역 겹침 (_CROSS_EXCHANGE_CONSUMERS 중복 라우팅)~~ | S13 US-232 (PositionRegistry 심볼 레벨 락) |
| ~~stat_arb 구조적 결함 (WFE=-1.03, cross_exchange 영역 겹침)~~ | S13 US-231 (z-score 하드스톱 3.5 + 레짐 게이트) |
| ~~AdaptiveThreshold WR 기반 피드백 루프 (WR>90%에서 edge 하향)~~ | S13 US-226 + S14 US-234 |
| ~~Auto-tuner 미작동 (ScheduledTuner 로그 미관찰)~~ | S13 US-226 (로깅 강화) + S14 US-234 (Shadow 미니 튜너) |
| ~~전략 간 포지션 충돌 (동일 symbol 동시 거래)~~ | S13 US-232 (PositionRegistry) |
| ~~전략별 자본 할당 없음 (per-strategy 한도 없음)~~ | S13 US-224 (loss_cap 차등) + US-228 (전략별 CB) |
| ~~cross_exchange MIN_EDGE 과소 (5bps vs round-trip 32-65bps)~~ | S13 US-235 (max_spread 100bps + min_book_depth 500) |
| ~~Coinone Rate Limit~~ | 자동 재연결 구현 |
| ~~빈 Orderbook 경고~~ | crash 없음, 무시 |
| ~~cex_dex 미구현~~ | S8 US-177 |

---

## 회귀 Phase (S1~S12) — TF QF/SF 발견

> S1~S6은 TF Quarter-Final(QF) 검증에서 발견된 원본 Phase(A~M)의 미비점 회귀 수정.
> S7~S12는 TF Semi-Final(SF) Stage 2 FAIL 후 추가 회귀 수정.
> 모든 Phase 완료. 현재 SSOT.md에서 Phase S13 진행 중.

### Phase S1: Security Hardening — US-123~128, US-152 ✅ ALL PASS (← J-EXT W1, E-1, D, I 보완)

- [x] US-152: **API 키 로테이션** + .gitignore 강화 + pre-commit hook (← J-EXT US-105,106 누락) ✅
- [x] US-123: 전 엔드포인트 JWT 인증 강제 (← J-EXT US-105,106 인증 범위 미완) ✅
- [x] US-124: JWT 시크릿 강화 + prod fail-fast (← J-EXT US-105 bcrypt fallback) ✅
- [x] US-125: Nginx IP whitelist + X-Forwarded-For (← Phase I US-075 인프라 보안) ✅
- [x] US-126: Redis 인증 + dangerous commands (← Phase E-1 US-042~044 모니터링 보안) ✅
- [x] US-127: CSP 헤더 강화 (← Phase D US-037~041 대시보드 보안) ✅
- [x] US-128: pytest backoff jitter 테스트 수정 (← Phase I US-074 Coinone 백오프) ✅

**Phase S1 완료 현황 (2026-03-14)**

| US | 제목 | 상태 | 결과 |
|----|------|------|------|
| US-152 | API 키 로테이션 + .gitignore 강화 + pre-commit hook | ✅ PASS | .env REDIS_PASSWORD 인라인 주석 버그 수정 |
| US-123 | 전 엔드포인트 JWT 인증 강제 | ✅ PASS | JWT 미들웨어 적용, 13 새로운 테스트 추가 |
| US-124 | JWT 시크릿 강화 + prod fail-fast | ✅ PASS | bcrypt 비밀번호 해싱 + DASHBOARD_PASSWORD 필수 |
| US-125 | Nginx IP whitelist + X-Forwarded-For 신뢰 | ✅ PASS | Nginx 설정: set_real_ip_from, trusted_proxies |
| US-126 | Redis 인증 + dangerous commands 비활성화 | ✅ PASS | Redis --requirepass CLI, COMMAND DISABLE |
| US-127 | CSP 헤더 강화 (Nginx + Next.js) | ✅ PASS | CSP: default-src 'self', script-src 'self' (no unsafe-eval) |
| US-128 | pytest backoff 테스트 수정 | ✅ PASS | Coinone 지터 백오프 테스트 jitter 구간 조정 |

**Shadow 검증 결과** (Stage D):
- **10분 Shadow**: uptime=613.1s, PnL=+$38.21, WR=93.75% (30/32), crash=0
- **QA 보안**: 8/8 PASS (JWT auth, Redis AUTH, CONFIG disabled, health/metrics public)
- **Docker**: .env REDIS_PASSWORD 인라인 주석 버그 수정, TimescaleDB WAL 파라미터 개선

**추가 수정사항**:
- `.env REDIS_PASSWORD` 인라인 주석 (# 포함) → `leviathan-redis-secret` 치환
- `docker-compose.yml` TimescaleDB `include_dir` 제거 → 개별 `-c` WAL 파라미터 적용

---

### Shadow 현실성 GAP 이력 (Phase SR — RESOLVED)

| ID | 심각도 | 이전 상태 | 해결 상태 |
|----|--------|----------|----------|
| SG-1 | ~~치명~~ | ~~partial_fill_rate=0.0, rejection_rate=0.0~~ | 0.05 / 0.02 활성화 (RESOLVED) |
| SG-2 | ~~치명~~ | ~~매수+매도 레그 동기 실행, 0ms 지연~~ | 50-300ms 랜덤 지연 활성 (RESOLVED) |
| SG-3 | ~~높음~~ | ~~PowerLawSlippage(k=0) — 오더북 깊이 미반영~~ | BookWalkSlippage VWAP 체결 활성 (RESOLVED) |
| SG-4 | ~~높음~~ | ~~무한 가상 잔고, 소진 추적 없음~~ | VirtualBalanceTracker + 리밸런스 (RESOLVED) |
| SG-5 | ~~높음~~ | ~~trade_size=Decimal("1") 하드코딩~~ | compute_depth_trade_size(L1깊이×0.10) (RESOLVED) |
| SG-6 | ~~중간~~ | ~~Rate limit 시뮬레이션 없음~~ | 거래소별 토큰 버킷 (RESOLVED) |

### Shadow 이력 (Phase E-2 US-047, 10min) — 아카이브

| 항목 | 값 |
|------|-----|
| 거래 수 | 2,325 (cross:1155, latency:1142, spot_futures:28) |
| 승률 | 96.5% (2243W / 82L) |
| PnL | +$39,733.58 |
| Crash | 0 (Traceback=0, CRITICAL=0) |
| 활성 전략 | 7개 등록+시작 |
| MIN_EDGE_BPS | 3 (SignalGenerator) |

### Shadow 이력 (Phase 7.3h-i, MIN_EDGE_BPS=5)

| 시간 | 거래 수 | 승률 | PnL (USDT) | DD |
|------|---------|------|------------|-----|
| 5min | 12 | 75% | +0.007 | 0.09% |
| 10min | 18 | 72% | +0.009 | 0.05% |
| 30min | 123 | 70% | +0.074 | 0.07% |
| 60min | 55 | 89% | +0.045 | ~0% |

---

### Phase S2: Engine Wiring Completion — US-129~134, US-153~155 ✅ ALL PASS (← E-3, J-EXT W3, K, M, B-5 보완)

- [x] US-129: RiskGuardian PortfolioState 실제 값 주입 (← Phase E-3 US-049 무력화) ✅
- [x] US-130: DynamicSizer 실행 경로 연결 (← J-EXT W3 US-114 미연결) ✅
- [x] US-131: RegimeDetector + ONNX Scorer main.py 주입 (← Phase K US-084 + Phase M US-094 미연결) ✅
- [x] US-132: SlippageFeedbackLoop LegResult 필드 수정 (← J-EXT W3 US-115 필드 불일치) ✅
- [x] US-133: AtomicOrderExecutor(IOC) main.py 연결 (← J-EXT W3 US-119 미연결) ✅
- [x] US-134: TCA/Correlation ExecutionResult 필드 통일 (← J-EXT W3 US-116,118 필드 불일치) ✅
- [x] US-153: **주문 중복 방지** Idempotency Key (← Phase B-5 US-027~029 누락) ✅
- [x] US-154: RiskGuardian max_concurrent_positions (← Phase E-3 US-049 체크 누락) ✅
- [x] US-155: Graceful shutdown 오픈 포지션 정리 (← Phase E-3 US-049~050 누락) ✅

**Shadow 검증 결과** (Stage D):
- **10분 Shadow**: uptime=912.5s, PnL=+$419.40, WR=85% (324/381), crash=0
- **코드리뷰**: 0 CRITICAL, 0 HIGH, 9 fixes applied
- **L0 수정**: _peak_equity None guard (main.py), blacklist re-registration loop fix (stale_detector.py)

### Phase S3: Infrastructure Hardening — US-135~139 ✅ ALL PASS (← A, E-1, E-2, SR 보완)

- [x] US-135: DB 스키마 통합 + 자동 마이그레이션 (docker/init.sql 통합, migration_runner.py with advisory lock + transaction) ✅ PASS
- [x] US-136: .env MIN_EDGE_BPS 동기화 + PowerLaw k (_check_env_sync in preflight.py, main.py에서 호출) ✅ PASS
- [x] US-137: Nginx WS 포트 + 백업 자동재시작 (docker-compose.yml backup services, restart:"no") ✅ PASS
- [x] US-138: Alertmanager 연결 + Grafana datasource (sed-based env var substitution, Telegram webhook) ✅ PASS
- [x] US-139: Docker 리소스 제한 + healthcheck (datasources.yml 프로비저닝, mem_limit 설정) ✅ PASS

**Shadow 검증 결과** (Stage D):
- **12분 Shadow**: uptime=720s, PnL=+$0.0069, WR=100% (4/4), crash=0
- **DB Migration**: advisory lock + transaction 적용, auto_ddl 검증 완료
- **인프라**: Docker 15 services ALL HEALTHY, Alertmanager→Telegram webhook 연결 완료

### Phase S4: Dashboard Completion — US-140~144 (← D, H, J-EXT W2 보완)

- [x] US-140: API prefix 통일 + SWR key — `/api/v1/` 통일, kill-switch/strategies/status 경로 수정
- [x] US-141: System 페이지 실데이터 연동 — system.py NEW (Docker containers + psutil resources), asyncio.to_thread
- [x] US-142: Heatmap/OrderbookView 175 심볼 연동 — symbols/spreads 엔드포인트, SpreadItem[] 변환
- [x] US-143: Strategy/Portfolio/EquityCurve mock 제거 — 전 컴포넌트 실데이터 연결, MOCK→OFFLINE
- [x] US-144: 대시보드 테스트 SWR v2 + 모바일 — isValidating 수정, TradeDetail 반응형 (w-full sm:w-80)

**Stage C 코드리뷰**: CRITICAL 3 + HIGH 5 → fix loop 12건 적용 → 전부 해결. 보안 CRITICAL 0.
**Shadow 검증 결과** (Stage D):
- **18.5h Shadow**: 289 trades, WR=90.7%, PnL=-$7.86 (stat_arb_v1 -$7.61 원인), crash=0
- **API QA**: 13/13 PASS — containers/resources/symbols/spreads 엔드포인트 + 인증 + 엣지케이스
- **stat_arb 손실 문제**: US-156 신규 생성 (SSOT §9 HIGH 등록). SHADOW_DISABLED_STRATEGIES .env 미설정이 원인.
- **pytest**: 4,360 passed, 0 failed, 6 skipped | tsc: 0 errors

### Phase S5: Data Pipeline & Auto-Tuner — US-145~148, US-156 ✅ ALL PASS (← E-2, E-3, SR 보완 + S4 Shadow 발견)

- [x] US-145: Auto-Tuner TimescaleDB async loader ✅ PASS
- [x] US-146: ScheduledTuner main.py 연결 ✅ PASS
- [x] US-147: Attribution TimescaleDB + materialized views ✅ PASS
- [x] US-148: Shadow MDD 비율 + Rebalancer balance feed ✅ PASS
- [x] US-156: Shadow 손실 전략 비활성화 — SHADOW_DISABLED_STRATEGIES .env 설정 ✅ PASS

**Shadow 검증 결과** (Stage D):
- **12분 Shadow**: uptime=721s, PnL=+0.2671 USDT, WR=95.3% (148/155), crash=0
- **Auto-Tuner**: TimescaleDB async loader 동작, ScheduledTuner 매주 실행 확인
- **Attribution**: 과거 거래 이력 TimescaleDB 조회 가능, materialized views 생성 완료
- **손실 전략 비활성화**: SHADOW_DISABLED_STRATEGIES 설정으로 stat_arb/spot_futures/latency_arb 비활성, Shadow PnL 양수 전환
- **pytest**: 4,474 passed, 0 failed, 6 skipped | tsc: 0 errors

### Phase S6: Documentation Sync — US-149~151 ✅ ALL PASS (← A 보완, 최후 실행)

- [x] US-149: prd.json 파일 경로 검증 — 0 mismatches 확인, total_stories=147 정합 ✅
- [x] US-150: CLAUDE.md 현행화 — Tests 4,460, PRD 145/2, 다음작업 TF재검증 ✅
- [x] US-151: SSOT.md 수식/체크 코드 동기화 — §4.3 이미 정합 확인, §4.2 ETH L2 비용 테이블 추가 ✅

### Phase S7: Pre-Live Hardening — US-157~168 ALL PASS (2026-03-15)

- [x] US-157: Config 아키텍처 분리 — engine/config/trading.json 생성, config.py 로더 ✅
- [x] US-158: okx_futures + bybit_futures 활성 거래소 추가 — trading.json active_exchanges ✅
- [x] US-159: _reconcile_loop 실제 구현 — shadow.py 60s 주기 잔고 비교 ✅
- [x] US-160: InMemoryEventBus 큐 크기 제한 — maxsize=10000, drop oldest ✅
- [x] US-161: KRW stale rate 거래 중단 로직 — _krw_stale 플래그 필터링 ✅
- [x] US-162: Auto-discovery 거래량 필터 — min_volume_usd in SignalConfig ✅
- [x] US-163: Dashboard 로그인 수정 — login page + next.config.js rewrites ✅
- [x] US-164: Shadow PnL 단일 손실 방어 — strategy temp disable (기본 0s, prod 설정 가능) ✅
- [x] US-165: Redis 연결 명시적 close — Engine.stop()에서 disconnect() ✅
- [x] US-166: 모니터링 가이드 문서 작성 — docs/operations/monitoring-guide.md ✅
- [x] US-167: Docker 리소스 제한 — Redis cpus:0.5, TimescaleDB cpus:1.0 ✅
- [x] US-168: httpx AsyncClient 재사용 — telegram.py, telegram_bot.py, bithumb_collector.py ✅

**Shadow 검증 결과** (Stage D):
- **10분 Shadow**: 2,230 trades, WR=93.3%, PnL=+$0.464, MaxDD=$0.222, crash=0
- **코드리뷰**: CRITICAL 1 + HIGH 2 발견 → 즉시 수정 (get_all_balances→summary, telegram close, min_volume_usd env)
- **테스트**: 4,471 passed, 0 failed, 6 skipped

### Phase S8: System Integration Hardening — US-169~180 ✅ ALL PASS (2026-03-15)

> **완료**: 구현되었으나 엔진 미연결 기능 12개 main.py 초기화 체인 연결 완료.
> CRITICAL 2건 수정 (OKX IOC ordType, KRW KillSwitch→soft-block) + HIGH 3건 수정
> Shadow 35min: PnL +$1.85, WR 92.2%, crash=0 | 4,587 tests passed

- [x] US-169: MultiStrategySignalProducer LIVE 모드 연결 — Paper/Shadow만 동작, LIVE에서 5/8 전략 신호 0건
- [x] US-170: Triangular Scanner 구현 — 전략 코드만 존재, 신호 생성 Scanner 부재 (GAP 7 해소)
- [x] US-171: KRW Staleness → soft-block 활성화 — 120초 stale 시 경고만 → 거래 신호 필터링
- [x] US-172: ONNX ML Scorer 신호 필터링 연결 — 로드만 하고 signal.py에서 호출 안 함
- [x] US-173: HMM RegimeDetector 신호 파이프라인 연결 — 초기화만, predict() 미호출 → 레짐 항상 NORMAL
- [x] US-174: AdaptiveThreshold 엔진 연결 — 94줄 구현 완료이나 main.py 미인스턴스화
- [x] US-175: ExposureTracker 인스턴스화 + RiskGuardian 연결 — 코드 존재하나 미생성
- [x] US-176: CorrelationMonitor → DynamicSizer 포지션 축소 연결 — Check #9 로그만
- [x] US-177: DEX 실연결 — _build_dex_adapter() 항상 None (GAP 8 해소)
- [x] US-178: IOC Limit Order 주요 거래소 구현 — Binance/Bybit/OKX native adapter
- [x] US-179: ScheduledTuner 핫리로드 + 기본 활성화 — 파라미터 재시작 없이 반영
- [x] US-180: InMemoryEventBus 큐 크기 제한 — maxsize 강제 + drop oldest

**추가 설정/환경 갭 수정 (S8 내 포함)**
- [x] TRADING_ACTIVE_EXCHANGES .env 동기화 (okx_futures, bybit_futures 추가)
- [x] strategy_activation.json Paper/Live에서도 적용
- [x] _reconcile_loop 거래소 API 잔고 대조 구현

### Phase S9: Strategy Activation — US-181~186 ✅ ALL PASS (2026-03-16)

> **완료**: TF QF 3차 FAIL(8/8 전략 중 4개 비활성화) 회귀. 4개 전략 evaluator 구현 + 전체 활성화.
> Shadow 10min: 7/7 전략 등록, 6/7 시그널 생산, 10/10 거래소, crash=0 | 4,588 tests passed

- [x] US-181: RealDataSignalProducer statistical_arb evaluator 구현 — rolling z-score(z=8.0, 200samples, 300s cooldown), Korean exchange 제외
- [x] US-182: RealDataSignalProducer latency_arb evaluator 구현 — LatencyTracker.lead_lag_pairs() + StaleDetector 교차검증
- [x] US-183: spot_futures/stat_arb/latency_arb disabled_strategies 해제 — trading.json `disabled_strategies: []`, Korean guard 유지
- [x] US-184: futures_futures stale spread 방어 — StaleDetector + 500bps 이상치 필터
- [x] US-185: StrategyValidation insufficient_data→unverified 분류 — ScheduledTuner cascade-disable 방지
- [x] US-186: 8개 전략 전체 Shadow 통합 검증 — 7/7 등록, 10/10 거래소, crash=0

**TF QF 중 추가 수정**
- [x] TRADING_ACTIVE_EXCHANGES 8→10개 (bybit_futures, okx_futures 추가)
- [x] InventoryRebalancer balance_feed NOT_CONNECTED → CONNECTED (connect_exchange_feeds 호출)
- [x] /health 엔드포인트 내부 상태(engine_running, kill_switch_active) 노출 제거 (보안)
- [x] SSOT.md/CLAUDE.md 테스트 수 4,587→4,589 동기화
- [x] spot_futures evaluator Korean exchange guard 추가 (upbit, bithumb, coinone 제외)

### Phase S10: Strategy Architecture Hardening — US-187~202 ✅ 완료 (2026-03-17)

> **회귀 사유**: TF SF Stage 2 (2H Shadow) PnL -$78.82 FAIL
> **근본 원인**: ① 전략 영역 겹침 (_CROSS_EXCHANGE_CONSUMERS) ② stat_arb = cross_exchange 동일 영역 ③ Auto-tuner/ML 미작동 ④ AdaptiveThreshold WR→PnL 전환 필요
> **완료 결과**: Shadow 10min PnL +$4.99, WR 93.3%, 3,423 trades, crash 0, overlap 0
> **회귀 후 경로**: S10 완료 ✅ → TF QF 재실행(단계 3.5 조립 검증 추가) → Phase S11(UI/UX) → TF SF 재시작

- [x] US-187: `_CROSS_EXCHANGE_CONSUMERS` 제거 + 신호 흐름 검증 — manager.py frozenset 제거, stat_arb/latency_arb RealDataSignalProducer 신호 수신 확인
- [x] US-188: stat_arb cross-asset pair 재설계 (2-3일) — BTC-ETH/ETH-SOL/BTC-BNB 고정 3쌍, Signal.metadata["symbol2"], _is_cointegrated fail-closed 수정
- [x] US-194: latency_arb → cross_exchange 병합 — LatencyArbStrategy 삭제, latency_boost 모드 통합, 전략 8→7개
- [x] US-201: AdaptiveThreshold WR→복합 지표(Expected Edge bps + Profit Factor) 기반 전환 — expected_edge_bps + PF 기반 조정, WR은 보조 지표
- [x] US-189: cross_exchange min_spread_bps 5→10 복원 — latency_boost 모드일 때 5bps 허용
- [x] US-195: 전략 간 포지션 충돌 방지 — (symbol, exchange_pair) 10초 윈도우 중복 체크, asyncio.Lock
- [x] US-196: 전략별 자본 할당 — trading.json capital_allocation_pct, RiskGuardian check #11
- [x] US-197: stat_arb ScheduledTuner EXCLUDED 제거 — US-188 완료 후 적용
- [x] US-199: 전략 overlap 감지 메트릭 — Prometheus counter, 10초 윈도우 감지
- [x] US-190: ScheduledTuner 작동 확인 — optuna/apscheduler import, 수동 트리거 --run-once
- [x] US-198: Korean exchange 필터 보강 — latency_boost + stat_arb cross-asset Korean 제외
- [x] US-191: ML/Tuning 컴포넌트 작동 로그 — AdaptiveThreshold PnL 로그, RegimeDetector 레짐 로그, ONNX 카운터
- [x] US-192: ExposureTracker Redis 연결 확인
- [x] US-200: 오토튜너 백테스트 리플레이 A/B 인프라 — event-level 데이터 저장, deterministic replay
- [x] US-202: 7개 전략 전체 Shadow 10min 재검증 — PnL +$4.99, WR 93.3%, 3423 trades, overlap=0, crash=0
- [x] US-193: §9 RESOLVED 이슈 → SSOT_COMPLETE.md 이관

### Phase S11: Operations UX Core — US-203~212 ✅ 완료 (2026-03-17)

> **목표**: 사장님이 대시보드 열자마자 3초 안에 상황 파악 가능하게
> **진입 조건**: Phase S10 완료 + TF QF PASS

- [x] US-203: MissionControlStrip — 40px 상시 상태바 (전 페이지 표시: EQUITY, TODAY PnL, WIN%, ACTIVE, MODE, KILL SWITCH)
- [x] US-204: Overview 페이지 재설계 — 3초 규칙 (총자산/PnL/시스템상태 → 전략 기여도 → PnL 곡선)
- [x] US-205: Strategies 페이지 — Health Score 카드 (0-100, WR 40pts + Fill 30pts + Signal 15pts + Error 15pts)
- [x] US-206: System/Operations 페이지 — 2-click Kill Switch (3초 카운트다운) + Docker/DB/Redis 상태 + 거래소 레이턴시
- [x] US-207: 텔레그램 한국어 템플릿 (인프라봇) — 가동 리포트, 장애 경보 양식
- [x] US-208: 텔레그램 한국어 템플릿 (거래봇) — 체결 알림, 일일 정산 리포트 양식
- [x] US-209: 텔레그램 심각도 필터링 — EMERGENCY=즉시, CRITICAL=1분/건, WARNING=5분/건, INFO=30분 배치
- [x] US-210: WebSocket payload 확장 — state_update에 total_equity, win_rate, active_strategy_count 추가
- [x] US-211: 9개 신규 API 엔드포인트 — portfolio/positions, daily-returns, system/logs, db-metrics, redis-metrics, alerts/acknowledge, alerts/resolve, settings/test-alert, exchanges/reconnect
- [x] US-212: 대시보드 백엔드 연동 검증 — 모든 페이지 실데이터 확인, 콘솔 에러 0건, 모바일 375px/768px

### Phase S12: Extended UX + Analytics — US-213~220 ✅ 완료 (2026-03-17)

> **목표**: 확장 UX + 고급 분석 + 알림 고도화
> **진입 조건**: Phase S11 완료

- [x] US-213: SmartTelegramAlerter — Redis 중복제거 + 배치 (알림 피로 근본 해결)
- [x] US-214: Analytics 페이지 — Sharpe 순위, 시간대별 히트맵, 신호 품질 분석
- [x] US-215: Alerts 페이지 — 인시던트 lifecycle (acknowledge/resolve), 심각도 필터
- [x] US-216: Portfolio Drawdown 차트 + Exposure Heatmap (전략×거래소 매트릭스)
- [x] US-217: Settings Danger Zone — Emergency Stop, Reset Defaults (적색 경계 분리)
- [x] US-218: 사이드바 재구성 — MONITOR/ANALYZE/MANAGE 3그룹
- [x] US-219: Telegram Bot 커맨드 — /pnl, /strategies, /risk, /pause, /resume, /alerts
- [x] US-220: 주간 자동 리포트 — 일요일 23:59 KST 자동 발송

### Phase S13: 기관급 전략 완전체 — US-221~233, US-235~243 (← TF SF 2차 FAIL + 6명 전문가 리뷰) ✅ 완료

> **목표**: 기관급 전략 완전체 구현. CRITICAL 버그 5개 수정 + 4계층 Stale 감지 + 전략별 CB + Auto-tuner 연동
> **회귀 사유**: TF SF 2차 Stage 2 FAIL — 2H45M PnL -$153.47, loss_capped 17건×-$50=-$850
> **전문가 리뷰**: Karina(아키텍트), Yeji(퀀트), Winter(비판), Wonyoung(테스트), Jisoo(보안), 디버거 + 외부 리서치 3팀
> **플랜**: `.claude/plans/snuggly-chasing-spark.md` (15 Part, 5라운드 검증)
> **진입 조건**: TF SF FAIL 확정

- [x] US-221: futures_futures 2차 freshness 검증 (← stale 17건 통과, threshold 3.0→1.5s + spread outlier)
- [x] US-222: per-strategy circuit breaker — 연속 손실 자동 쿨다운 (← -$50×4건 연속 8초)
- [x] US-223: spot_futures + funding_rate 비활성화 (← WR 42%, WR 6.7%)
- [x] US-224: loss_cap 전략별 차등 — futures $3, cross_exchange $7 (← $50×17건=-$850, $70 자본 기준 재설계)
- [x] US-225: futures spread outlier filter — >100bps WARNING, >200bps 블랙리스트 60s (← fake spread 진입)
- [x] US-226: CRITICAL 버그 5개 수정 — funding_rate=0.0 하드코딩, estimate_cost 슬리피지, AdaptiveThreshold 지연, RegimeDetector 미연결, Auto-tuner 검증
- [x] US-227: 4계층 Stale 감지 — 타임스탬프 분리 + 하트비트 EMA + 시퀀스 갭 + 스프레드 정상성
- [x] US-228: 전략별 서킷브레이커 상태머신 — ACTIVE/THROTTLED/HALTED/SUSPENDED + 복합 점수 + FIA 2024
- [x] US-229: spot_futures/funding_rate 시그널 레벨 사전 필터 강화 + cex_dex 명시적 비활성화
- [x] US-230: 스프레드 이상치 필터 — 적응형 롤링 중앙값 + 타임스탬프 교차검증 300ms
- [x] US-231: stat_arb z-score 하드스톱 3.5 + Kalman stale 가드 + 레짐 게이트 (학계 합의 Park 2026)
- [x] US-232: 전략 간 충돌 방지 — PositionRegistry 심볼 레벨 락 + 우선순위 계층
- [x] US-233: futures_futures 전용 강화 — min_spread 15bps + 호가 깊이 + 노셔널 캡
- [x] US-235: cross_exchange 미세 조정 — max_spread 100bps + min_book_depth 500
- [x] US-236: 엔진 Dead Wiring 전수 수정 — stat_arb Dead Code 연결, _position_manager 초기화, Redis 오타, PortfolioState 미연결
- [x] US-237: 대시보드 정합성 + 로그인 수정 — CORS/CSP, Alert API 경로, ParameterSlider, JWT 검증
- [x] US-238: spot_futures 로직 개선 — Korean stale 보정 + basis 최적화 + 백테스트 검증
- [x] US-239: funding_rate 로직 개선 — 시그널 빈도 증가 + diff threshold 최적화 + 백테스트 검증
- [x] US-240: statistical_arb 거래 전환 개선 — 마찰력 재계산 + z-score threshold 최적화 + 백테스트 검증
- [x] US-241: triangular 로직 개선 — cycle 감지 임계값 재설계 + 백테스트 검증
- [x] US-242: cex_dex 인프라 구성 + 로직 검증 — DEX RPC 설정 + 백테스트
- [x] US-243: 7개 전략 통합 백테스트 + 복합지표 검증 — 전 전략 동시 1시간 Shadow PASS

> **Shadow 1시간 (US-243 완료 기준)**: 9,338 trades, PnL +$1,674.06, WR 76.2%, crash 0
> **전략별**: spot_futures 7,021 ✅ / futures_futures 2,117 ✅ / triangular 122 ✅ / stat_arb 73 ✅ / funding_rate 5 ✅

### Phase S14: Auto-tuner 완전 연동 — US-234 ✅ 완료

> **목표**: AdaptiveThreshold + RegimeDetector Shadow 통합 + Optuna 미니 튜너
> **진입 조건**: Phase S13 완료

- [x] US-234: AdaptiveThreshold + RegimeDetector Shadow 통합 + Auto-tuner 미니 튜너

---

### TF QF 검증 이력 (S1~S12 기간)

> **#1 FAIL (2026-03-13)**: CRITICAL 9, HIGH 12, MEDIUM 19, LOW 19 → 회귀 Phase S1~S6 생성
> **#2 조건부 PASS (2026-03-15)**: CRITICAL 9→0, HIGH 12→0 (91.5% 해소), 33/35 US PASS
> **#4 PASS (2026-03-16)**: S9에서 4개 전략 evaluator 구현 완료
> **#5 FAIL (2026-03-17)**: 단계 3.5 알림 Dead Wiring 미탐지 → 6차 재수행
> **#6 PASS (2026-03-17)**: CRITICAL 0, HIGH 0, MEDIUM 6, LOW 4 (자금 손실 경로 0건)

### TF SF 이력 (S1~S12 기간)

> **1차 Stage 1 PASS / Stage 2 FAIL (2026-03-16)**: 2H PnL -$78.82 (stat_arb -$127, 전략 영역 겹침) → Phase S10 생성
> **2차 Stage 1 PASS / Stage 2 FAIL (2026-03-17)**: 2H45M PnL -$153.47, loss_capped 17건 → Phase S13 생성
