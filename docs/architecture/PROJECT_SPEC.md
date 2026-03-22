# LEVIATHAN 프로젝트 설계서 (PROJECT_SPEC)

> **버전**: 1.0 | **최종 갱신**: 2026-03-22
> **SSOT 참조**: `SSOT.md` (유일한 활성 설계 문서)
> **이 문서의 목적**: 프로젝트를 처음 접하는 사람이 전체 시스템을 이해할 수 있는 종합 설계서

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [최종 프로그램 형태 (5-Layer Architecture)](#2-최종-프로그램-형태-5-layer-architecture)
3. [모듈 구조](#3-모듈-구조)
4. [데이터 흐름](#4-데이터-흐름)
5. [운영 모드 전환](#5-운영-모드-전환)
6. [배포 아키텍처](#6-배포-아키텍처)
7. [전략 매트릭스](#7-전략-매트릭스)
8. [리스크 관리](#8-리스크-관리)
9. [모니터링 + 장애 복구](#9-모니터링--장애-복구)
10. [수학 모델 요약](#10-수학-모델-요약)
- [부록 A: 거래소별 수수료](#부록-a-거래소별-수수료)
- [부록 B: 환경변수 (주요)](#부록-b-환경변수-주요)
- [부록 C: 엔진 시작 시퀀스 (11단계)](#부록-c-엔진-시작-시퀀스-11단계)

---

## 1. 프로젝트 개요

### 1.1 미션

**LEVIATHAN**은 글로벌 암호화폐 거래소 간 크로스 차익거래를 자동 실행하는 고빈도 거래 엔진이다. 10개 거래소의 실시간 호가 데이터를 수집하고, 7개 전략으로 차익 기회를 탐지하며, 마찰력 모델로 순수익을 검증한 뒤 자동 체결한다.

### 1.2 기술 스택

| 항목 | 기술 | 비고 |
|------|------|------|
| **엔진** | Python 3.12+ (AsyncIO) + Rust (PyO3) | Hot-path는 Rust, 나머지 Python |
| **대시보드** | Next.js 14 (App Router) + TypeScript | JWT 인증 + 실시간 WebSocket 피드 |
| **시계열 DB** | TimescaleDB (PostgreSQL 16) | 거래 이력, OHLCV, 스프레드 |
| **캐시/Pub-Sub** | Redis 7.2 | 실시간 상태, 이벤트 버스 |
| **모니터링** | Prometheus + Grafana + Loki | 30일 메트릭 보관 + 로그 집계 |
| **알림** | Telegram 3-Bot + Alertmanager | TradeBot/DevBot/InfraBot |
| **ML** | HMM + XGBoost + ONNX Runtime | 레짐 탐지 + 신호 스코어링 |
| **역방향 프록시** | Nginx (TLS + Rate Limiting) | IP 화이트리스트 + 보안 헤더 |
| **컨테이너** | Docker Compose (15 서비스) | 전체 인프라 단일 배포 |
| **워크플로우 자동화** | 순수 Python (SQLite + jsonschema) | 체크포인팅 + 일관성 검사 |

### 1.3 거래소 (10개 네이티브 WebSocket 어댑터)

ccxt를 사용하지 않고, 각 거래소의 WebSocket API에 직접 연결하는 네이티브 어댑터를 구현했다.

| # | 거래소 | 유형 | WS 엔드포인트 | 통화 |
|---|--------|------|--------------|------|
| 1 | Binance | Spot | `wss://stream.binance.com:9443` | USDT |
| 2 | Binance Futures | Futures | `wss://fstream.binance.com` | USDT |
| 3 | Bybit | Spot | `wss://stream.bybit.com/v5/public/spot` | USDT |
| 4 | Bybit Futures | Futures | `wss://stream.bybit.com/v5/public/linear` | USDT |
| 5 | OKX | Spot | `wss://ws.okx.com:8443/ws/v5/public` | USDT |
| 6 | OKX Futures | Futures | `wss://ws.okx.com:8443/ws/v5/public` | USDT |
| 7 | Bitget | Spot | `wss://ws.bitget.com/v2/ws/public` | USDT |
| 8 | Upbit | Spot | `wss://api.upbit.com/websocket/v1` | KRW |
| 9 | Bithumb | Spot | `wss://pubwss.bithumb.com/pub/ws` | KRW |
| 10 | Coinone | Spot | `wss://stream.coinone.co.kr` | KRW |

> KRW 거래소(Upbit, Bithumb, Coinone)는 `CollectorManager.KOREAN_EXCHANGES`에 의해 자동으로 `/USDT` -> `/KRW` 심볼 매핑되며, ShadowMode에서 KRW -> USDT 역환산(dual-source: Upbit+Bithumb API, 30s 갱신)을 수행한다.

---

## 2. 최종 프로그램 형태 (5-Layer Architecture)

```
+============================================================================+
|                         Layer 5: Presentation                              |
|  Next.js 14 Dashboard  |  Telegram 3-Bot  |  Grafana  |  CLI (leviathan)  |
+============================================================================+
|                         Layer 4: API Gateway                               |
|  FastAPI REST (/api/v1/*)  |  WebSocket (/ws, /ws/feed)  |  JWT Auth      |
|  Nginx TLS Proxy  |  Rate Limiting  |  IP Whitelist                       |
+============================================================================+
|                         Layer 3: Business Logic                            |
|  7 Strategies  |  RiskGuardian (11-check)  |  KillSwitch (3-tier)         |
|  SignalGenerator  |  AtomicExecutor  |  PortfolioRisk  |  CapitalAllocator|
|  RegimeDetector  |  AdaptiveThreshold  |  CorrelationMonitor              |
+============================================================================+
|                         Layer 2: Data Pipeline                             |
|  10 WS Collectors  |  PriceHub  |  CostCalculator  |  DataQualityManager |
|  FundingRateCollector  |  SymbolDiscovery  |  KRW Normalizer              |
+============================================================================+
|                         Layer 1: Infrastructure                            |
|  TimescaleDB  |  Redis (EventBus)  |  Prometheus  |  Loki  |  Docker      |
|  WAL Backup  |  DB Backup  |  Alertmanager  |  MonitorDaemon              |
+============================================================================+
```

### Layer 1: Infrastructure (인프라)

시스템의 기반이 되는 데이터 저장소, 메시징, 모니터링 인프라.

- **TimescaleDB**: 거래 이력(`execution_log` hypertable, 90일 retention), OHLCV, 스프레드 시계열 데이터 저장
- **Redis 7.2**: 실시간 상태 캐시, EventBus(Pub/Sub), 포지션 캐시. Live 모드에서는 Redis EventBus, Paper/Shadow에서는 InMemoryEventBus 사용
- **Prometheus + Grafana**: 엔진 메트릭 수집(`/metrics` 엔드포인트), 30일 보관, 4개 대시보드(Overview/전략별/거래소/ML)
- **Loki + Promtail**: 크로스 컨테이너 로그 집계 및 검색
- **WAL Backup + DB Backup**: TimescaleDB WAL 아카이빙(RPO < 1시간) + 일일 pg_dump(7일 보관)
- **MonitorDaemon**: 5분 주기 Redis/DB/API 헬스체크 (bot-gateway 컨테이너에서 실행)

### Layer 2: Data Pipeline (데이터 파이프라인)

거래소에서 실시간 데이터를 수집하고, 정제하여 전략에 공급하는 계층.

- **10개 WS Collectors**: 거래소별 네이티브 WebSocket 어댑터. 오더북 + 체결 데이터 실시간 수신
- **PriceHub**: 모든 거래소의 오더북을 중앙 집약하여 최적 가격 계산
- **CostCalculator**: 마찰력 모델 — 수수료 + 슬리피지 + 네트워크 비용 + 롤백 비용 통합 계산
- **DataQualityManager**: StaleDetector + HealthChecker 통합. 거래소별 차등 freshness 관리, anomaly detection
- **FundingRateCollector**: 4개 선물 거래소의 펀딩레이트 수집
- **SymbolDiscovery**: 거래소 간 공통 심볼 자동 탐색 (`min_exchanges=3`)
- **KRW Normalizer**: 한국 거래소 KRW -> USDT 실시간 환율 변환 (dual-source, 30s 갱신, +/-10% sanity)

### Layer 3: Business Logic (비즈니스 로직)

차익거래 기회 탐지, 리스크 검증, 주문 실행의 핵심 계층.

- **7개 전략**: cross_exchange, spot_futures, futures_futures, triangular, funding_rate, statistical_arb, cex_dex (상세: [7. 전략 매트릭스](#7-전략-매트릭스))
- **SignalGenerator**: PriceHub의 오더북 데이터에서 CEXOrderbookSlippage 필터를 적용하여 유효한 차익 신호만 통과
- **RiskGuardian**: 11개 사전 거래 검증 체크 (상세: [8. 리스크 관리](#8-리스크-관리))
- **KillSwitch**: 3-tier 긴급 정지 (Tier 1 < 1ms halt, Tier 2 < 500ms 주문 취소, Tier 3 < 2s 포지션 청산)
- **AtomicExecutor**: 양 레그 동시 IOC 체결 + Partial Fill 대응 (delta 불균형 시 시장가 청산)
- **PortfolioRisk**: 전략간 PnL 상관 행렬(30min rolling), 상관 > 0.7 합산 제한, 포트폴리오 VaR
- **CapitalAllocator**: Kelly 기준 전략별 자본 배분 (거래 이력 30건 이상 후 활성화)
- **RegimeDetector**: HMM 기반 시장 레짐 분류 (CALM/VOLATILE/CRISIS -> 전략별 파라미터 자동 조정)
- **AdaptiveThreshold**: 롤링 백분위수 + 변동성 가중치 기반 동적 MIN_EDGE 조정
- **CorrelationMonitor**: 전략간 상관관계 실시간 추적, 높은 상관 시 포지션 스케일다운

### Layer 4: API Gateway (API 게이트웨이)

외부 인터페이스를 제공하는 계층.

- **FastAPI REST**: `/api/v1/shadow/stats`, `/api/v1/portfolio-summary`, `/api/v1/portfolio/equity-curve` 등
- **WebSocket**: `/ws` (1초 간격 state_update), `/ws/feed` (거래/신호/경고 실시간 이벤트)
- **JWT 인증**: PyJWT 기반, `JWT_SECRET`/`DASHBOARD_USER`/`DASHBOARD_PASSWORD` 환경변수
- **Nginx**: TLS 종단, Rate Limiting, IP 화이트리스트, 보안 헤더 (CSP, HSTS 등)

### Layer 5: Presentation (프레젠테이션)

사용자가 시스템을 모니터링하고 제어하는 인터페이스.

- **Next.js 14 Dashboard**: PortfolioSummary, RiskGauge, PerformanceTrend, EventFeed, GlobalHeatmap, OrderbookView, ModeSwitch, EquityCurve, Attribution, System Health 등
- **Telegram 3-Bot**: TradeBot(20개 명령, 거래 알림 + Kill Switch), DevBot(16개 명령 + Watchdog `/go`), InfraBot(7개 명령, psutil 리소스)
- **Grafana**: Overview/전략별 상세/거래소 상태/ML 모델 성능 4개 대시보드
- **CLI**: `python -m src.main start/stop/status`, `python -m src.workflow.cli check_all/checkpoint`

---

## 3. 모듈 구조

엔진의 소스 코드는 `engine/src/` 아래 15개 하위 디렉토리로 구성된다.

```
engine/src/
  main.py                   # 엔진 진입점 (Engine 클래스, 초기화 시퀀스)
  bot_gateway.py            # DevBot + InfraBot + MonitorDaemon 독립 실행
  |
  +-- collectors/           # [Layer 2] 10개 거래소 WebSocket 수집기
  |   +-- base_collector.py           # 기본 수집기 인터페이스
  |   +-- binance_collector.py        # Binance Spot
  |   +-- binance_futures_collector.py # Binance Futures
  |   +-- bybit_collector.py          # Bybit Spot
  |   +-- bybit_futures_collector.py  # Bybit Futures
  |   +-- okx_collector.py            # OKX Spot
  |   +-- okx_futures_collector.py    # OKX Futures
  |   +-- bitget_collector.py         # Bitget Spot
  |   +-- upbit_collector.py          # Upbit (KRW)
  |   +-- bithumb_collector.py        # Bithumb (KRW)
  |   +-- coinone_collector.py        # Coinone (KRW)
  |   +-- funding_rate_collector.py   # 선물 펀딩레이트 수집
  |   +-- symbol_discovery.py         # 심볼 자동 탐색
  |   +-- manager.py                  # CollectorManager (전체 수집기 관리)
  |
  +-- core/                 # [Layer 2-3] 핵심 데이터 구조 + 가격/신호 파이프라인
  |   +-- config.py                   # Settings, ExecutionMode, DataMode
  |   +-- models.py                   # OrderSide, TradeRequest 등 공유 모델
  |   +-- events.py                   # EventBus (Redis/InMemory)
  |   +-- order_book.py               # OrderBook 데이터 구조
  |   +-- price_hub.py                # 거래소별 오더북 중앙 집약
  |   +-- signal.py                   # SignalGenerator (CEXOrderbookSlippage 필터)
  |   +-- multi_signal.py             # MultiStrategySignalProducer (8 전략 신호)
  |   +-- real_signal_producer.py     # 실 데이터 기반 신호 생산
  |   +-- adaptive_threshold.py       # 롤링 백분위수 동적 MIN_EDGE
  |   +-- capital_allocator.py        # Kelly 기준 자본 배분
  |   +-- data_quality_manager.py     # 데이터 품질 통합 관리
  |   +-- depth_analyzer.py           # 오더북 깊이 분석 (VWAP, 유동성)
  |   +-- balance_tracker.py          # VirtualBalanceTracker
  |   +-- inventory_rebalancer.py     # 거래소간 재균형
  |   +-- latency_tracker.py          # 거래소별 지연 추적
  |   +-- metrics_collector.py        # 내부 메트릭 수집
  |   +-- metrics_rolling.py          # 롤링 윈도우 Sharpe/Calmar/Sortino
  |   +-- market_impact.py            # Temporary + Permanent 마켓 임팩트
  |   +-- ou_process.py               # Ornstein-Uhlenbeck 프로세스 추정
  |   +-- portfolio_risk.py           # 포트폴리오 리스크 매니저
  |   +-- position_registry.py        # 포지션 레지스트리
  |   +-- stale_detector.py           # 오더북 staleness 탐지
  |   +-- triangle_finder.py          # 삼각 차익 경로 탐색
  |   +-- triangular_scanner.py       # 삼각 차익 실시간 스캐너
  |   +-- live_gate_continuous.py     # LiveGate 연속 평가
  |   +-- rust_bridge.py              # Rust PyO3 브릿지 (feature flags)
  |   +-- engine.py                   # 엔진 유틸리티
  |
  +-- strategies/           # [Layer 3] 7개 차익거래 전략
  |   +-- base.py                     # 전략 기본 인터페이스
  |   +-- manager.py                  # StrategyManager (등록/활성화)
  |   +-- cross_exchange.py           # 거래소간 차익 (latency_boost 모드 포함)
  |   +-- spot_futures.py             # 현물-선물 베이시스
  |   +-- futures_futures.py          # 선물-선물 스프레드
  |   +-- triangular.py              # 삼각 차익 (Bellman-Ford)
  |   +-- funding_rate.py             # 펀딩레이트 차익 (OU 프로세스)
  |   +-- statistical_arb.py          # 통계적 차익 (공적분 기반)
  |   +-- cex_dex.py                  # CEX-DEX 차익 (조건부 활성)
  |   +-- latency_arb.py              # deprecated shim (cross_exchange로 병합)
  |
  +-- execution/            # [Layer 3] 주문 실행 + 리콘실리에이션
  |   +-- executor.py                 # AtomicExecutor (양 레그 동시 IOC)
  |   +-- atomic.py                   # AtomicOrderExecutor (Live IOC)
  |   +-- paper.py                    # PaperExecutor (가상 체결)
  |   +-- paper_adapter.py            # Paper 모드 거래소 어댑터
  |   +-- trade_consumer.py           # TradeRequestConsumer
  |   +-- sizer.py                    # DynamicSizer (주문 크기 계산)
  |   +-- reconciler.py               # 포지션 리콘실리에이션
  |   +-- position_recovery.py        # 포지션 리커버리 (재시작 복원)
  |
  +-- risk/                 # [Layer 3] 리스크 관리
  |   +-- guardian.py                 # RiskGuardian (11-check pre-trade)
  |   +-- kill_switch.py              # KillSwitch (3-tier 긴급 정지)
  |   +-- circuit_breaker.py          # CircuitBreaker (CLOSED/OPEN/HALF_OPEN)
  |   +-- correlation_monitor.py      # 전략간 상관관계 추적
  |   +-- exposure_tracker.py         # 순 익스포저 추적
  |   +-- per_strategy_cb.py          # 전략별 서킷 브레이커
  |   +-- position_manager.py         # 포지션 매니저
  |   +-- slippage.py                 # 슬리피지 추정
  |
  +-- friction/             # [Layer 2] 마찰력 모델
  |   +-- cost_calculator.py          # CostCalculator (총 마찰력 계산)
  |   +-- fee_model.py                # FeeModel (거래소별 수수료)
  |   +-- slippage_model.py           # CEXOrderbookSlippage (통계적 시장 영향)
  |   +-- slippage_feedback.py        # Slippage Feedback Loop (실체결 vs 예상)
  |   +-- dex_cost.py                 # DEX 비용 모델 (가스비 등)
  |
  +-- infra/                # [Layer 1] 인프라 유틸리티
  |   +-- telegram.py                 # Telegram 알림 클라이언트
  |   +-- telegram_bot_base.py        # 봇 공통 기반 클래스
  |   +-- telegram_trade_bot.py       # TradeBot (20개 명령)
  |   +-- telegram_dev_bot.py         # DevBot (16개 명령 + Watchdog)
  |   +-- telegram_infra_bot.py       # InfraBot (7개 명령)
  |   +-- telegram_charts.py          # 차트 이미지 생성
  |   +-- metrics.py                  # Prometheus 메트릭 정의
  |   +-- logger.py                   # structlog 설정
  |   +-- compliance.py               # ComplianceChecker (23항목)
  |   +-- startup_checker.py          # 시작 전 환경 검증
  |   +-- monitor_daemon.py           # MonitorDaemon (5분 주기 헬스체크)
  |
  +-- api/                  # [Layer 4] REST API + WebSocket
  |   +-- server.py                   # FastAPI 앱 + EngineContext
  |   +-- auth.py                     # JWT 인증 (PyJWT)
  |   +-- middleware.py               # CORS, Rate Limiting
  |   +-- websocket.py                # WebSocket 핸들러
  |
  +-- modes/                # [Layer 3] 실행 모드
  |   +-- shadow.py                   # ShadowMode (실 데이터 + 가상 체결 + 전체 지표)
  |   +-- live_gate.py                # LiveGate (6-check AND 게이트)
  |   +-- progressive_shadow.py       # 단계별 Shadow 테스트 (Stage 1~6)
  |   +-- preflight.py                # Preflight 체크 (시작 전 검증)
  |   +-- strategy_validation.py      # 전략 유효성 검증
  |
  +-- ml/                   # [Layer 3] ML 파이프라인
  |   +-- hmm_trainer.py              # HMM 레짐 분류 학습
  |   +-- xgb_trainer.py              # XGBoost 신호 스코어링 학습
  |   +-- onnx_exporter.py            # ONNX 모델 내보내기
  |   +-- onnx_runtime.py             # ONNX 추론 런타임
  |   +-- feature_pipeline.py         # 20-feature ML 파이프라인
  |   +-- feature_store.py            # 피처 저장소
  |   +-- canary.py                   # ML Canary 검증
  |
  +-- analysis/             # [Layer 3] 분석 + 기여도
  |   +-- attribution.py              # 전략별/거래소별 수익 기여도 분석
  |   +-- tca.py                      # Transaction Cost Analysis
  |   +-- walk_forward.py             # Walk-Forward 검증
  |   +-- ml_backtest.py              # ML 모델 백테스트
  |   +-- signal_analyzer.py          # 신호 품질 분석
  |
  +-- tuning/               # [Layer 3] 파라미터 튜닝
  |   +-- scheduled_tuner.py          # ScheduledTuner (Optuna 자동 최적화)
  |   +-- regime_detector.py          # RegimeDetector (HMM 기반)
  |   +-- adaptive_threshold.py       # AdaptiveThreshold (동적 임계치)
  |   +-- optimizer.py                # Optuna 최적화 엔진
  |   +-- evaluator.py                # 백테스트 평가
  |   +-- backtest.py                 # 백테스트 프레임워크
  |   +-- strategy_backtest.py        # 전략별 백테스트
  |   +-- data_loader.py              # TimescaleDB 데이터 로더
  |   +-- file_data_loader.py         # 파일 기반 데이터 로더
  |   +-- param_bridge.py             # 파라미터 브릿지
  |   +-- shadow_runner.py            # Shadow 실행기
  |   +-- ab_replay.py                # A/B 리플레이 테스트
  |
  +-- workflow/             # [Layer 1] 워크플로우 자동화
  |   +-- checkpoint_engine.py        # 체크포인트 엔진 (SQLite)
  |   +-- consistency.py              # SSOT <-> PRD <-> State 3-Way 검증
  |   +-- fsm.py                      # 유한 상태 기계
  |   +-- state_schema.py             # 상태 스키마 정의
  |   +-- sync.py                     # 동기화 유틸리티
  |   +-- cli.py                      # CLI (check_all, checkpoint)
  |
  +-- dex/                  # [Layer 2] DEX 통합 (조건부)
  |   +-- mock_adapter.py             # DEX 목 어댑터
  |
  +-- cli/                  # [Layer 5] CLI 도구
      +-- leviathan_cli.py            # 메인 CLI (start/stop/status)
      +-- paper_runner.py             # Paper 모드 실행기
      +-- sandbox_paper_runner.py     # Sandbox Paper 실행기
      +-- sandbox_verify.py           # Sandbox 검증
      +-- backtest_cli.py             # 백테스트 CLI
      +-- tune_cli.py                 # 튜닝 CLI
```

---

## 4. 데이터 흐름

### 4.1 메인 파이프라인 (신호 탐지 -> 체결)

```
[거래소 WS] --orderbook/trade-->
  [Collectors (10개)] --raw data-->
    [PriceHub] --aggregated book-->
      [DataQualityManager] --filtered (stale/anomaly 제거)-->
        [SignalGenerator + CEXOrderbookSlippage] --valid signals-->
          [MultiStrategySignalProducer] --strategy signals-->
            [7 Strategies] --TradeRequest-->
              [RiskGuardian (11-check)] --approved-->
                [AtomicExecutor] --IOC orders-->
                  [거래소 API] --fills-->
                    [PnL Tracker + Reconciler]
```

### 4.2 Shadow 모드 데이터 흐름

```
[실 WebSocket 데이터] -->
  [동일 파이프라인 (Paper 체결)] -->
    [ShadowMode._metrics] --get_snapshot()-->
      [EngineContext.shadow_mode] -->
        [REST: /api/v1/shadow/stats] + [WS: _dashboard_feed_loop (1s)]
          --> [Dashboard ShadowPanel.tsx]
          --> [Prometheus 메트릭]
          --> [LiveGate 6-check 자동 평가]
```

### 4.3 대시보드 실시간 피드

```
[Engine] --1s interval-->
  [WS /ws] --state_update JSON-->
    [Dashboard] --React state-->
      [PortfolioSummary, RiskGauge, PerformanceTrend, EventFeed, GlobalHeatmap]

[Engine] --event-driven-->
  [WS /ws/feed] --trade/signal/alert JSON-->
    [Dashboard EventFeed 컴포넌트]
```

### 4.4 ML 파이프라인

```
[PriceHub + OrderBook 데이터] -->
  [MLFeaturePipeline (20 features)] -->
    [ONNX Scorer] --score-->
      [SignalGenerator 보조 필터]

[거래 이력 (TimescaleDB)] -->
  [HMM Trainer] --학습-->
    [RegimeDetector (CALM/VOLATILE/CRISIS)] -->
      [전략별 파라미터 자동 조정]

[거래 이력] -->
  [XGBoost Trainer] --학습-->
    [ONNX Export] -->
      [실시간 추론]
```

---

## 5. 운영 모드 전환

### 5.1 4단계 모드

```
Backtest ──> Paper ──> Shadow ──> Live
(합성 데이터)  (실 WS,     (실 WS,       (실 거래,
              가상 체결)   가상 체결      LiveGate
                         + 전체 지표)    통과 후)
```

| 모드 | DATA_MODE | EXECUTION_MODE | EventBus | 어댑터 | 지표 | LiveGate |
|------|-----------|----------------|----------|--------|------|----------|
| **Backtest** | `synthetic` | paper | InMemory | Paper | 없음 | 없음 |
| **Paper** | `real_public` | paper | InMemory | Paper | 없음 | 없음 |
| **Shadow** | `shadow` | paper | InMemory | Paper | Prometheus + TimescaleDB | 6-check |
| **Live** | `real_authenticated` | live | Redis | Native | 전체 | 상시 |

### 5.2 Paper vs Shadow 차이 (핵심)

| 구분 | Paper | Shadow |
|------|-------|--------|
| **목적** | 파이프라인 기능 검증 ("작동하는가?") | 수익성 검증 ("돈이 되는가?") |
| **데이터** | 실 WebSocket (real_public) | 실 WebSocket (shadow) |
| **실행** | PaperExecutor (가상) | PaperExecutor (가상) |
| **지표** | 없음 | Prometheus + TimescaleDB 전체 기록 |
| **LiveGate** | 없음 | 6-check 게이트 평가 |
| **Telegram** | 없음 | 일일 요약 + 알림 |

### 5.3 LiveGate 전환 기준 (6-check AND)

Shadow 모드에서 아래 6개 조건을 **모두** 만족해야 Live 전환이 허용된다.

| # | 체크 | 임계값 | 의미 |
|---|------|--------|------|
| 1 | Sharpe (7일 롤링) | >= 2.5 | 위험 대비 수익이 충분한가 |
| 2 | Max Drawdown | < 5% | 최대 손실이 허용 범위인가 |
| 3 | 일일 신호 수 | >= 100/day | 전략이 충분히 활성인가 |
| 4 | Kill Switch | Not halted | 긴급 정지 상태가 아닌가 |
| 5 | Circuit Breaker | CLOSED | 서킷 브레이커가 정상인가 |
| 6 | 거래소 Health | >= 95% | 거래소 연결이 안정적인가 |

### 5.4 Progressive Shadow 프로토콜

24시간 단계적 검증으로 조기 문제 발견:

```
Stage 1: 1H  (튜너 OFF) --> 기본 동작 확인 (crash=0, 신호 흐름 정상)
Stage 2: 2H  (튜너 OFF) --> 승률/PnL 추세 안정성 (WR>60%, PnL 양수)
Stage 3: 2H  (튜너 ON)  --> 오토튜너 비교 (PROVEN/NEUTRAL/HARMFUL/BUG)
Stage 4: 6H  (최적 설정) --> 전략별 메트릭 분리 + 마찰력 정확도 검증
Stage 5: 12H              --> 메모리 누수/리소스 사용량 안정성
Stage 6: 24H              --> LiveGate 6-check + Sharpe>2.0, MDD<5%, 일일 PnL 양수
```

---

## 6. 배포 아키텍처

### 6.1 Docker Compose (15 서비스)

```yaml
# docker-compose.yml 서비스 목록 (실제 파일 기준)
services:
  engine          # 거래 엔진 (REST 8000 + WS)
  redis           # Redis 7.2 (6379)
  redis-exporter  # Redis Prometheus 메트릭 (9121)
  timescaledb     # TimescaleDB PG16 (5432)
  dashboard       # Next.js 14 (3000)
  prometheus      # Prometheus (9090, 30일 보관)
  alertmanager    # Alertmanager (9093, 3봇 토큰 sed)
  nginx           # Nginx TLS 프록시 (80, 443)
  bot-gateway     # DevBot + InfraBot + MonitorDaemon
  auto-tuner      # Optuna 자동 튜닝 스케줄러
  db-backup       # 일일 pg_dump (7일 보관)
  loki            # 로그 집계 (3100)
  promtail        # 로그 수집 -> Loki
  wal-backup      # WAL 아카이빙 + PITR
  grafana         # 메트릭 시각화 (3001)
```

### 6.2 의존성 그래프

```
                          +--------+
                          | nginx  |
                          +---+----+
                              |
              +---------------+---------------+
              |               |               |
         +----v----+    +----v-----+    +----v----+
         | engine  |    | dashboard|    | grafana |
         +----+----+    +----+-----+    +----+----+
              |               |               |
    +---------+---------+     |          +----+
    |                   |     |          |
+---v---+          +----v-----v----+    +v-----------+
| redis |          | timescaledb   |    | prometheus  |
+---+---+          +-------+-------+    +------+------+
    |                      |                   |
+---v----------+    +------v------+    +-------v--------+
| redis-export |    | db-backup   |    | alertmanager   |
+--------------+    | wal-backup  |    +----------------+
                    +-------------+
                                       +------+------+
                                       |    loki     |
                                       +------+------+
                                              |
                                       +------v------+
                                       |  promtail   |
                                       +-------------+

[bot-gateway]  -- 독립 실행 (engine 의존 없음)
[auto-tuner]   -- redis + timescaledb 의존
```

### 6.3 리소스 제한

| 서비스 | 메모리 | CPU | 비고 |
|--------|--------|-----|------|
| engine | 2GB | 2.0 | 가장 많은 리소스 사용 |
| timescaledb | 4GB | 1.0 | WAL 아카이빙 활성 |
| redis | 1GB | 0.5 | AOF + RDB 영속화 |

### 6.4 볼륨

```
redis_data          # Redis 영속화
timescaledb_data    # PostgreSQL 데이터
wal_archive         # WAL 아카이브 (PITR)
prometheus_data     # Prometheus TSDB (30일)
grafana_data        # Grafana 대시보드/플러그인
loki_data           # Loki 로그 인덱스
db_backups          # pg_dump 백업 (7일)
promtail_positions  # Promtail 오프셋
```

---

## 7. 전략 매트릭스

### 7.1 Cross-Exchange Arbitrage (`cross_exchange.py`)

| 항목 | 내용 |
|------|------|
| **원리** | 동일 자산이 서로 다른 거래소에서 다른 가격에 거래될 때, 싼 곳에서 사고 비싼 곳에서 파는 고전적 차익거래 |
| **수익 구조** | 거래소간 스프레드 - 양측 수수료 - 슬리피지 - 네트워크 전송 비용 |
| **리스크** | 가격 변동 (레그간 지연), 네트워크 전송 지연, 거래소 장애 |
| **파라미터** | `MIN_EDGE_BPS` (동적 AdaptiveThreshold), `latency_boost` 모드 (US-194 병합) |
| **상태** | 활성 (기관급 25% 자본 배분) |
| **예상 수익** | 연 5-25% |

### 7.2 Spot-Futures Basis (`spot_futures.py`)

| 항목 | 내용 |
|------|------|
| **원리** | 현물과 선물의 베이시스(가격차)가 비정상적으로 벌어졌을 때, 수렴을 기대하고 반대 포지션 진입. OU 프로세스 모델링 |
| **수익 구조** | 베이시스 수렴 차익 + 펀딩레이트 수취 (숏 선물 시) |
| **리스크** | 베이시스 확대 (디레버리징), 마진 콜, 펀딩레이트 역전 |
| **파라미터** | `basis_threshold_bps`, `max_holding_hours` (양 레그 동시 청산), OU half-life |
| **상태** | 대기 (기관급 15%, 시장 조건 대기) |
| **예상 수익** | 연 8-30% |

### 7.3 Futures-Futures Spread (`futures_futures.py`)

| 항목 | 내용 |
|------|------|
| **원리** | 서로 다른 선물 거래소의 같은 자산 가격 차이를 활용. 펀딩레이트 수렴 기대 |
| **수익 구조** | 거래소간 선물 스프레드 수렴 + 펀딩 차이 |
| **리스크** | 마진 요구 증가, 스프레드 확대, stale 데이터 |
| **파라미터** | `spread_threshold_bps`, `funding_convergence`, `enable_stale_guard` |
| **상태** | 활성 (기관급 20%) |
| **예상 수익** | 연 5-15% |

### 7.4 Triangular Arbitrage (`triangular.py`)

| 항목 | 내용 |
|------|------|
| **원리** | 단일 거래소 내에서 3개 통화 쌍(A/B -> B/C -> C/A)을 순환 거래하여 환율 불일치 차익 포착. Bellman-Ford 알고리즘으로 최적 경로 탐색 |
| **수익 구조** | 삼각 순환 차익 - 3회 수수료 - 슬리피지 |
| **리스크** | 실행 속도 (3개 주문 순차), 유동성 부족, leg별 통화 크기 불일치 |
| **파라미터** | `min_profit_pct`, `latency_budget_ms=500`, Bellman-Ford fee weight `-log(1-fee)` |
| **상태** | 대기 (수학 오류 S15에서 수정 완료) |
| **예상 수익** | 연 2-10% |

### 7.5 Funding Rate Arbitrage (`funding_rate.py`)

| 항목 | 내용 |
|------|------|
| **원리** | 선물 거래소간 펀딩레이트 차이를 활용. 높은 펀딩레이트 거래소에서 숏, 낮은 거래소에서 롱. OU 프로세스로 다음 펀딩 예측하여 사전 진입 |
| **수익 구조** | 펀딩레이트 차이 수취 (8시간 주기) |
| **리스크** | 펀딩레이트 급변, 포지션 유지 비용, settlement 타이밍 |
| **파라미터** | `min_funding_diff_bps`, z-score filter, 8H rolling history, OU half-life 기반 차단 |
| **상태** | 검증됨 (기관급 30%, 4거래소 x 8심볼) |
| **예상 수익** | 연 15-30% |

### 7.6 Statistical Arbitrage (`statistical_arb.py`)

| 항목 | 내용 |
|------|------|
| **원리** | 공적분(cointegration) 관계에 있는 자산 쌍의 스프레드가 평균에서 이탈했을 때 평균회귀를 기대하고 진입. Z-score 기반 진입/청산 |
| **수익 구조** | 스프레드 평균회귀 차익 |
| **리스크** | 공적분 관계 붕괴, 레짐 변화, OOS(Out-of-Sample) 성능 저하 |
| **파라미터** | `z_entry`, `z_exit`, 거래비용 조정 z-score (비용 > 예상수익 시 스킵), RegimeDetector CRISIS 차단 |
| **상태** | 검증됨 (WFE -1.03, stat_arb DISABLED — US-297) |
| **예상 수익** | 연 11-16% (정상 시장) |

### 7.7 CEX-DEX Arbitrage (`cex_dex.py`)

| 항목 | 내용 |
|------|------|
| **원리** | 중앙화 거래소(CEX)와 탈중앙화 거래소(DEX)의 가격 차이 활용. DEX의 AMM 비효율 포착 |
| **수익 구조** | CEX-DEX 스프레드 - 가스비 - 슬리피지 |
| **리스크** | 가스비 급등, 프론트러닝(MEV), DEX 유동성 부족 |
| **파라미터** | `DEX_RPC_URL` 환경변수 설정 시만 활성화 |
| **상태** | 비활성 (DEX 비용 문제, 추후 검토) |
| **예상 수익** | 연 10-50% (가스비 의존) |

---

## 8. 리스크 관리

### 8.1 KillSwitch 3-Tier 긴급 정지

```
Tier 1 (< 1ms):    threading.Event.set() + Redis SET
                    --> 즉시 신규 주문 차단 (외부 의존성 없음)
                    --> Rust AtomicBool도 동시 설정 (PyO3)

Tier 2 (< 500ms):  asyncio.gather로 전 거래소 미체결 주문 취소
                    --> 2초 timeout per exchange

Tier 3 (< 2000ms): asyncio.gather로 전 거래소 오픈 포지션 시장가 청산
                    --> 3초 timeout per exchange
```

- **트리거**: MDD 초과, 거래소 장애, Telegram 수동 명령, Alertmanager 알림
- **해제**: 수동만 가능 (자동 해제 없음, 안전 우선)
- **Prometheus**: `KILL_SWITCH_ACTIVE` gauge (alerts.yml 참조)

### 8.2 RiskGuardian 11-Check Pre-Trade

모든 거래 요청은 체결 전에 RiskGuardian의 11개 체크를 **순서대로** 통과해야 한다.

| # | 체크 | 설명 | 바이패스 |
|---|------|------|----------|
| **0** | Halt | `threading.Event` 기반 KillSwitch 확인 | **불가** |
| 1 | Position Limit | 심볼별 포지션 한도 초과 여부 | |
| 2 | Drawdown Limit | 현재 드로다운이 임계값 미만인지 | |
| 3 | Exposure Limit | 총 익스포저 한도 확인 | |
| 4 | Circuit Breaker | 서킷 브레이커 상태 = CLOSED | |
| 4e | Net Exposure | 자산별 순 익스포저 한도 (Amendment 7) | |
| 5 | Exchange Health | 대상 거래소 health score >= 임계값 | |
| 6 | Max Single Trade | 단일 거래 크기 한도 | |
| 7 | Volatility | 1분 변동성 vs 24시간 평균 비교 | |
| 8 | Rollback Cost | 예상 롤백 비용이 임계값 미만 (Amendment 3C) | |
| 9 | Correlation | 전략간 상관관계 스케일다운 (log-only) | |
| 10 | Max Concurrent | 최대 동시 포지션 수 (US-154) | |

### 8.3 CircuitBreaker

```
CLOSED ──(연속 실패 N회)--> OPEN ──(300s cooldown)--> HALF_OPEN ──(성공)--> CLOSED
                                                          └──(실패)--> OPEN
```

- 전략별 개별 서킷 브레이커 (`per_strategy_cb.py`)도 지원
- Shadow 13항목 복합지표에서 CLOSED 상태 확인 필수

### 8.4 PortfolioRisk

- **상관 행렬**: 전략간 PnL 30분 롤링 상관 행렬 계산
- **합산 제한**: 상관 > 0.7인 전략 쌍의 합산 포지션 제한
- **포트폴리오 VaR**: 전체 포트폴리오 Value at Risk 실시간 계산
- **MDD 관리**: 전체 MDD 3% -> 신규 진입 차단, 5% -> 전체 청산
- **Regime-Aware 자본 배분**: CALM -> 공격적, VOLATILE -> 보수적, CRISIS -> 방어적

### 8.5 DataQualityManager (DQM)

- **중앙 통합**: StaleOrderbookDetector + HealthChecker 단일 진입점
- **차등 Freshness**: CEX-CEX 500ms, Korean 1s, 기본 2s
- **Health Score**: WS 연결 + 메시지 빈도 + 지연 -> 0-100점, < 80 비활성
- **Anomaly Detection**: 30초 롤링 평균 대비 +/-5% -> 3초 격리 후 재확인
- **Bithumb 특화**: 소형코인 2-10x 가격 오차 패턴 탐지 -> fake spread 거부

---

## 9. 모니터링 + 장애 복구

### 9.1 Prometheus 메트릭 (주요)

| 메트릭 | 유형 | 설명 |
|--------|------|------|
| `leviathan_trades_total` | Counter | 전략별/거래소별 거래 수 |
| `leviathan_signals_total` | Counter | 전략별 신호 수 |
| `leviathan_pnl_total` | Gauge | 전략별 누적 PnL |
| `leviathan_mdd_pct` | Gauge | 현재 Max Drawdown (%) |
| `leviathan_sharpe_ratio` | Gauge | 7일 롤링 Sharpe |
| `leviathan_execution_latency_ms` | Histogram | 신호 -> 체결 지연 |
| `leviathan_exchange_health` | Gauge | 거래소별 health score |
| `leviathan_kill_switch_active` | Gauge | Kill Switch 상태 (0/1) |
| `leviathan_risk_rejections_total` | Counter | RiskGuardian 거절 수 |
| `leviathan_circuit_breaker_state` | Gauge | CB 상태 (0=CLOSED, 1=OPEN) |

### 9.2 LiveGate 6-Check (상시 평가)

Shadow/Live 모드에서 지속적으로 계산되며, 미달 시 자동 FAIL 판정:

1. **Sharpe >= 2.5** (7일 롤링)
2. **MDD < 5%**
3. **일일 신호 >= 100**
4. **KillSwitch = Not halted**
5. **CircuitBreaker = CLOSED**
6. **거래소 Health >= 95%**

### 9.3 Shadow 13항목 복합지표

Stage B Shadow 10분 검증에 사용되는 복합지표:

| # | 체크 | 임계값 | 유형 |
|---|------|--------|------|
| 1 | crash | = 0 | 시스템 |
| 2 | 무중단 실행 | >= 10분 | 시스템 |
| 3 | PnL | >= $0 | 기본 (참고용) |
| 4 | Max Drawdown | < 5% (자본 대비) | 절대 지표 |
| 5 | Profit Factor | > 1.0 (총이익/총손실) | 절대 지표 |
| 6 | 신호 수 | >= 100/day (외삽) | 활성도 |
| 7 | Kill Switch | Not halted | 방어 레이어 |
| 8 | Circuit Breaker | CLOSED | 방어 레이어 |
| 9 | 거래소 Health | >= 95% | 인프라 |
| 10 | loss_capped | = 0 | 리스크 |
| 11 | 전략별 trade | 모든 활성 전략 trade >= 1 | 통합 검증 |
| 12 | 방어 레이어 활성 | CB/StaleDetector/OutlierFilter 로그 >= 1건 | 통합 검증 |
| 13 | 결과 파일 | `.omc/state/shadow-result-latest.json` 존재 | 검증 증거 |

### 9.4 장애 시나리오 + 대응

| 시나리오 | 자동 대응 | 수동 대응 |
|---------|----------|----------|
| **거래소 WS 끊김** | 자동 재연결 (fast_backoff), Health score 하락 | InfraBot `/health` 확인 |
| **MDD > 3%** | 신규 진입 차단, Telegram WARNING | 전략 비활성화 검토 |
| **MDD > 5%** | KillSwitch Tier 1~3 자동 실행, Telegram CRITICAL | DevBot `/go` 수동 재개 |
| **DB 장애** | 거래 지속 (메모리 캐시), WAL 복구 | `backup_db.sh` 또는 PITR |
| **Redis 장애** | InMemoryEventBus 폴백, KillSwitch Python-only | Redis 재시작 |
| **엔진 크래시** | Docker `restart: unless-stopped` | DevBot `/restart` |
| **Partial Fill** | AtomicExecutor delta 불균형 감지 -> 시장가 청산 | 수동 포지션 정리 |

### 9.5 Telegram 3-Bot

| 봇 | 토큰 환경변수 | 명령 수 | 역할 |
|----|-------------|---------|------|
| **TradeBot** | `TRADE_TELEGRAM_BOT_TOKEN` | 20개 | 거래 알림, Kill Switch, 포지션/체결/전략 제어, 일일 리포트 |
| **DevBot** | `DEV_TELEGRAM_BOT_TOKEN` | 16개 | 원격 개발 제어, Watchdog `/go` 수동 재개, `/cmd` 화이트리스트 명령 |
| **InfraBot** | `INFRA_TELEGRAM_BOT_TOKEN` | 7개 | 인프라 모니터링 (`/health`, `/resources` psutil, `/metrics`, `/restart`) |

- **TradeBot**: engine 컨테이너 내부에서 실행
- **DevBot + InfraBot**: bot-gateway 컨테이너에서 독립 실행 (engine 장애에도 작동)
- **Alertmanager**: 3봇 토큰 sed 치환으로 알림 라우팅

---

## 10. 수학 모델 요약

### 10.1 마찰력 모델

총 마찰력은 아래 공식으로 계산된다 (`friction/cost_calculator.py`):

```
Net_Profit = Gross_Spread
           - Fee_Buy - Fee_Sell
           - Slippage_Buy - Slippage_Sell
           - Network_Cost
           - Funding_Cost
           - Opportunity_Cost
           - E[Rollback_Cost]

E[Rollback_Cost] = P(rollback) * Avg_Rollback_Cost
P(rollback): 30-trade 롤링 윈도우, cold-start 기본값 5%
```

- **FeeModel**: 거래소별 taker_fee + network_cost(동적 transfer_coin)
- **CostCalculator**: `paper_`/`sandbox_` prefix 자동 strip
- Net_Profit > 0인 신호만 체결 허용

### 10.2 슬리피지 2계층

두 계층은 서로 다른 질문에 답하며, PnL 계산에서 더해지지 않는다.

**계층 1: 사전 필터 (CEXOrderbookSlippage)**
```
impact_fraction = sigma * k * sqrt(size / ADV)
expected_abs = impact_fraction * mid_price
CI: size/ADV <= 1.0 -> +/-20%
    size/ADV 1~3    -> +/-50%
    size/ADV 3~10   -> +/-100%
    size/ADV > 10   -> DO NOT TRADE
Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-gamma)  [t_0=60s, gamma=0.5]
```
- 용도: SignalGenerator에서 신호 허용/차단 기준. fill_price에 미반영.

**계층 2: 실행 시뮬레이션 (BookWalkSlippage)**
```
실제 오더북 깊이를 워킹하여 VWAP 체결가 산출
-> fill_price를 결정하는 실행 계층
```
- 용도: PaperExecutor에서 실체결 시뮬레이션. 통계 모델이 아닌 실행 시뮬레이션.

> **금지**: PowerLawSlippage(k > 0)를 PaperExecutor에 적용하는 것은 이중 계산이므로 금지. 현재 k=0으로 비활성.

### 10.3 Sharpe 비율 (연간화)

```
Sharpe = (mu - rf) / sigma * sqrt(periods_per_year)
mu = mean(hourly_returns)
sigma = std(hourly_returns)
periods_per_year = 8760 (1시간 윈도우, 24/7 암호화폐 시장)
rf = 0 (무위험 이자율 미적용)
```

### 10.4 Maximum Drawdown

```
MDD = max_t { (Peak_t - Cumulative_PnL_t) / Peak_t }
```

- Peak_t: 시점 t까지의 최고 누적 PnL
- DB 영속화: peak_equity를 TimescaleDB에 저장하여 재시작 시 복원

### 10.5 KillSwitch 타이밍

```
Tier 1: threading.Event.set()     -> < 0.01ms (Python)
        + Rust AtomicBool.store() -> < 0.001ms (PyO3, 선택)
        + Redis SET               -> < 1ms (네트워크)
        -----------------------------------------------
        총: < 1ms (신규 주문 차단)

Tier 2: asyncio.gather(cancel_all_orders)
        -> per exchange 2s timeout
        -----------------------------------------------
        총: < 500ms (병렬 실행)

Tier 3: asyncio.gather(close_all_positions)
        -> per exchange 3s timeout
        -----------------------------------------------
        총: < 2000ms (병렬 실행)
```

### 10.6 OU 프로세스 (Ornstein-Uhlenbeck)

펀딩레이트 및 베이시스 평균회귀 모델링:

```
dX_t = theta * (mu - X_t) * dt + sigma * dW_t

theta: 평균회귀 속도 (mean-reversion speed)
mu: 장기 평균 (long-run mean)
sigma: 변동성 (volatility)
Half-life = ln(2) / theta

Half-life < Execution Latency -> 차단 (평균회귀 너무 빠름)
```

---

## 부록 A: 거래소별 수수료

### Spot/Futures 수수료 (Tier 0, Taker 기준)

| 거래소 | Maker | Taker | 비고 |
|--------|-------|-------|------|
| Binance | 0.10% | 0.10% | |
| Bybit | 0.10% | 0.10% | Spot VIP0 |
| OKX | 0.08% | 0.10% | |
| Bitget | 0.10% | 0.10% | |
| Upbit | 0.05% | 0.139% | KRW 마켓 |
| Bithumb | 0.25% | 0.25% | KRW 마켓 |
| Coinone | 0.02% | 0.02% | API 할인 적용 (기본 0.20%) |

### ETH 출금 비용 (네트워크별)

| 거래소 | ETH 비용 | 네트워크 | 비고 |
|--------|---------|---------|------|
| Binance | $0.06 | Arbitrum One | L2 최저 경로 |
| Bybit | $0.19 | Arbitrum | L2 |
| OKX | $0.10 | Arbitrum | L2 |
| Bitget | $0.10 | Arbitrum | L2 |
| Upbit | $4.50 | Ethereum L1 | L2 미지원 |
| Bithumb | $2.50 | Ethereum L1 | L2 미지원 |
| Coinone | $2.50 | Ethereum L1 | L2 미지원 |

> 글로벌 거래소 $0.06~$0.19 (Arbitrum L2) vs KRW 거래소 $2.50~$4.50 (L1 only). 최적 루트: binance_futures -> coinone/upbit/bithumb (0.05%+0.02%=7bps)

---

## 부록 B: 환경변수 (주요)

### 엔진 설정 (`engine/.env`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ENGINE_ENV` | `dev` | 실행 환경 (`dev`/`staging`/`prod`/`test`) |
| `EXECUTION_MODE` | `paper` | 실행 모드 (`paper`/`live`) |
| `DATA_MODE` | `synthetic` | 데이터 소스 (`synthetic`/`real_public`/`shadow`/`real_authenticated`) |
| `DATABASE_URL` | - | TimescaleDB 연결 문자열 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 연결 문자열 |
| `REDIS_PASSWORD` | - | Redis 인증 비밀번호 |
| `JWT_SECRET` | - | JWT 서명 비밀키 |
| `DASHBOARD_USER` | - | 대시보드 로그인 사용자 |
| `DASHBOARD_PASSWORD` | - | 대시보드 로그인 비밀번호 |
| `BTC_REFERENCE_PRICE` | `50000` | BTC 기준 가격 (USDT -> BTC 변환) |
| `MIN_EDGE_BPS` | `5` | 최소 차익 임계값 (bps) |
| `TRADING_ACTIVE_EXCHANGES` | (auto) | 활성 거래소 목록 (JSON array) |
| `TRADING_SYMBOL_MIN_EXCHANGES` | `3` | 심볼 자동 탐색 최소 거래소 수 |
| `CAPITAL_INITIAL_CAPITAL` | - | 초기 자본 (USD) |
| `SHADOW_DISABLED_STRATEGIES` | - | Shadow에서 비활성화할 전략 (CSV) |
| `SLIPPAGE_GAMMA` | - | 슬리피지 감쇠 파라미터 |

### Telegram 설정

| 변수 | 설명 |
|------|------|
| `TRADE_TELEGRAM_BOT_TOKEN` | TradeBot 토큰 |
| `TRADE_TELEGRAM_CHAT_ID` | TradeBot 채팅 ID |
| `DEV_TELEGRAM_BOT_TOKEN` | DevBot 토큰 |
| `DEV_TELEGRAM_CHAT_ID` | DevBot 채팅 ID |
| `INFRA_TELEGRAM_BOT_TOKEN` | InfraBot 토큰 |
| `INFRA_TELEGRAM_CHAT_ID` | InfraBot 채팅 ID |

### 튜닝/ML 설정

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ENABLE_INLINE_TUNER` | `false` | 인라인 튜너 활성화 |
| `TUNER_N_TRIALS` | `100` | Optuna 시행 횟수 |
| `TUNER_DATA_SOURCE` | `timescaledb` | 튜너 데이터 소스 |
| `DEX_RPC_URL` | - | DEX RPC 엔드포인트 (설정 시 cex_dex 활성) |

> **주의**: `engine/.env`(엔진용)와 루트 `.env`(Docker용) 두 파일이 존재하며, 반드시 동기화해야 한다.

---

## 부록 C: 엔진 시작 시퀀스 (11단계)

`engine/src/main.py` -> `Engine.run()` 메서드의 초기화 시퀀스:

```
Engine.run()
  |
  +-- 1. _init_config()              # Settings 로드 (env vars + trading.json)
  |                                    # ENGINE_ENV, EXECUTION_MODE, DATA_MODE 결정
  |
  +-- 2. _init_infrastructure()       # EventBus 초기화 (Live=Redis, 나머지=InMemory)
  |                                    # TimescaleDB 커넥션 풀, HTTP 클라이언트
  |                                    # Telegram 클라이언트 초기화
  |
  +-- 3. _init_exchanges()            # 실행 모드에 따른 어댑터 초기화
  |                                    # Paper: PaperAdapter, Live: NativeAdapter
  |                                    # 10개 거래소 WebSocket 연결 시작
  |
  +-- 4. _init_signal_pipeline()      # PriceHub 초기화 (오더북 중앙 집약)
  |                                    # CostCalculator 초기화 (마찰력 계산)
  |                                    # SignalGenerator 초기화 (CEXOrderbookSlippage 필터)
  |                                    # RegimeDetector, AdaptiveThreshold, DQM 초기화
  |
  +-- 5. _init_strategies()           # StrategyManager에 7개 전략 등록
  |                                    # 전략별 RegimeDetector, AdaptiveThreshold 주입
  |                                    # latency_arb -> cross_exchange 병합 shim
  |
  +-- 6. _init_risk()                 # RiskGuardian 초기화 (11-check)
  |                                    # CircuitBreaker (300s cooldown)
  |                                    # KillSwitch (3-tier)
  |                                    # CorrelationMonitor, ExposureTracker
  |                                    # PortfolioRisk 초기화
  |
  +-- 7. _init_execution()            # AtomicExecutor 초기화 (양 레그 IOC)
  |                                    # TradeRequestConsumer 시작
  |                                    # DynamicSizer, DepthAnalyzer 연결
  |
  +-- 8. _populate_context()          # EngineContext에 모든 서브시스템 참조 설정
  |                                    # API 서버에서 접근 가능하도록 바인딩
  |                                    # Attribution, CapitalAllocator 연결
  |
  +-- 9. _startup_position_scan()     # 기존 포지션 복원 (PositionRecovery)
  |     + _startup_compliance_audit() # ComplianceChecker 23항목 시작 시 검사
  |
  +-- 10. _start_background_tasks()   # Health check loop (10s 간격)
  |                                    # Reconciliation loop (60s 간격)
  |                                    # Heartbeat loop (5s 간격)
  |                                    # ShadowMode + LiveGate 평가 (Shadow 모드)
  |                                    # API 서버 (uvicorn, port 8000)
  |                                    # TradeBot poll_loop (engine 내부)
  |                                    # HMM/XGBoost 학습 루프
  |                                    # MonitorDaemon (5분 주기)
  |
  +-- 11. _init_tuner()               # ScheduledTuner 초기화 (Optuna, 선택)
  |
  +-- await shutdown_event            # SIGTERM/SIGINT 또는 KillSwitch 대기
  |
  +-- stop()                          # Graceful shutdown
                                       # TradeConsumer 정지
                                       # StrategyManager 정지
                                       # Live 모드: 미체결 주문 취소
                                       # 거래소 연결 해제
                                       # ShadowMode, LiveGate, MarketRecorder 정지
                                       # DB 풀, HTTP 클라이언트, Redis 닫기
                                       # Telegram 클라이언트 닫기
                                       # ScheduledTuner 정지
                                       # Background tasks 취소 (10s timeout)
```

---

> **참고**: 이 문서는 SSOT.md, engine/src/main.py, docker-compose.yml, 각 모듈 소스 코드를 기반으로 작성되었으며, 코드 변경 시 함께 갱신해야 한다.
