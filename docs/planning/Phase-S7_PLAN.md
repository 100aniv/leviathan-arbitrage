# Phase S7 + TF Final 연속 실행 계획

> 작성일: 2026-03-15
> 목적: TF Final 전 잔여 이슈 해결 → TF Final [단계 2] Progressive Shadow 2H+ 진행

---

## 배경

TF Final [단계 1] 전체 시스템 체크리스트 **ALL PASS** (2026-03-15).
Progressive Shadow Stage 1 (8h28m, 502 trades, crash=0) PASS.
사장님 요청: "#1 PnL 손실 방어 + #3 okx_futures/bybit_futures 추가" 선행 해결 후 TF Final 계속.

잔여 이슈 출처:
- TF Semi-Final 재검증 MEDIUM ~7건
- TF Final [단계 1] 압박면접 RISK 4건
- TF Final [단계 1] NOTE 3건

---

## Phase S8: Pre-Live Hardening (US-157~166, 10개 US)

### US 목록

| US | 제목 | 우선순위 | 출처 | 핵심 변경 |
|----|------|---------|------|----------|
| US-157 | Config 아키텍처 분리 (.env→JSON) | P1 | 사장님 지시 | `engine/config/trading.json` 신설, .env는 시크릿만 |
| US-158 | okx_futures/bybit_futures 활성화 | P1 | TF Final NOTE#1 | config에 10개 거래소 명시 |
| US-159 | _reconcile_loop 실제 구현 | P2 | TF Final NOTE#2 | main.py:1768-1776 stub→실제 reconciliation |
| US-160 | InMemoryEventBus 크기 제한 | P2 | 압박면접 RISK#1 | asyncio.Queue(maxsize=10000) |
| US-161 | KRW stale rate 거래 중단 | P2 | 압박면접 RISK#4 | 120s stale 시 KRW 심볼 거래 일시 중단 |
| US-162 | Auto-discovery 거래량 필터 | P2 | 압박면접 RISK#5 | min_volume_usd=100000 24h 필터 추가 |
| US-163 | Dashboard 로그인 수정 | P1 | 사장님 보고 | NEXT_PUBLIC_ENGINE_URL + CORS 점검 |
| US-164 | Shadow PnL 단일 손실 방어 | P1 | TF Final Shadow 결과 | max_single_loss_usd 파라미터 + 자동 전략 제외 |
| US-165 | Redis 연결 명시적 close | P3 | TF Final NOTE#3 | graceful shutdown에 Redis close 추가 |
| US-166 | 모니터링 가이드 문서 | P1 | 사장님 요청 | docs/operations/monitoring-guide.md |

### 구현 순서 (의존성 기반)

```
배치 1 (P1, 독립): US-157 (Config분리) + US-163 (Dashboard 로그인) + US-166 (모니터링 가이드)
배치 2 (P1, US-157 의존): US-158 (okx/bybit futures) + US-164 (단일 손실 방어)
배치 3 (P2, 독립): US-159 (reconcile) + US-160 (EventBus) + US-161 (KRW stale) + US-162 (volume filter)
배치 4 (P3): US-165 (Redis close)
```

---

## US-157: Config 아키텍처 분리 (핵심 변경)

### 현재 문제
`.env`에 60+개 항목이 혼재: API 키(민감) + 거래소 목록/전략 파라미터(비민감)
상용 프로그램 관행: `.env`는 시크릿/인프라만, 비즈니스 설정은 JSON/YAML config.

### 변경 계획

**신규 파일**: `engine/config/trading.json`
```json
{
  "active_exchanges": ["binance","bybit","okx","bitget","upbit","bithumb","coinone","binance_futures","okx_futures","bybit_futures"],
  "disabled_strategies": ["statistical_arb_v1","spot_futures_v1","latency_arb_v1"],
  "risk": {
    "max_position_usd": 1000,
    "max_daily_loss_usd": 500,
    "max_net_exposure_per_asset": 5000,
    "min_edge_bps": 5,
    "min_price_usd": 0.10,
    "max_rollback_threshold": 0.02
  },
  "slippage": {
    "model": "cex_orderbook",
    "k_default": 1.0,
    "powerlaw_k": 0.0,
    "conservative_multiplier": 1.5,
    "gamma": 0.5,
    "gamma_calibrated": false
  },
  "execution": {
    "leg_timeout_ms": 1000,
    "rollback_timeout_ms": 2000,
    "reconciliation_interval_s": 5,
    "recovery_reconciliation_interval_s": 60
  },
  "phase_gates": {
    "phase": "alpha",
    "alpha_capital_per_exchange": 70,
    "beta_capital_per_exchange": 750
  },
  "symbol_discovery": {
    "mode": "auto",
    "min_exchanges": 3,
    "min_volume_usd": 100000
  }
}
```

**`.env` 잔류 항목** (시크릿/인프라만):
- API 키: BINANCE_*, OKX_*, BYBIT_*, UPBIT_*, BITHUMB_*, COINONE_*
- DB: DATABASE_URL, DB_*, REDIS_*
- 인프라: ENGINE_ENV, ENGINE_LOG_LEVEL, ENGINE_API_PORT, ENGINE_WS_PORT
- 인증: JWT_SECRET, DASHBOARD_*, TELEGRAM_*, WORKFLOW_TELEGRAM_*
- GitHub/Exa: GITHUB_TOKEN, EXA_API_KEY
- Rust 플래그: USE_RUST_*
- KRW_USDT_RATE (런타임 자동 갱신)

**코드 변경**:
- `engine/src/core/config.py`: `TradingConfig` 클래스에 JSON 로딩 추가 (env 우선, JSON fallback)
- `engine/src/main.py`: `_init_config()`에서 `config/trading.json` 로드
- 기존 `.env` 변수는 하위호환 유지 (env가 있으면 env 우선, 없으면 JSON fallback)

### 하위호환 전략
```
우선순위: 환경변수 > trading.json > 하드코딩 기본값
```
기존 .env로 동작 중인 시스템은 변경 없이 동작. trading.json은 점진적 마이그레이션.

---

## US-163: Dashboard 로그인 수정

### 조사 포인트
1. `NEXT_PUBLIC_ENGINE_URL` 설정 확인 (dashboard/.env.local)
2. CORS_ORIGINS에 대시보드 URL 포함 여부
3. JWT 쿠키 도메인/경로 설정
4. nginx 프록시 통과 시 헤더 전달

### 예상 원인
- `NEXT_PUBLIC_ENGINE_URL` 미설정 → API 호출 실패
- CORS 설정 불일치
- nginx에서 /api/ 프록시 시 cookie 도메인 이슈

---

## US-164: Shadow PnL 단일 손실 방어

### 변경
- `engine/config/trading.json`에 `max_single_loss_usd: 50` 파라미터 추가
- ShadowMode에서 단일 거래 손실 > threshold 시 해당 심볼 30분 쿨다운
- 로그 경고 + Telegram 알림

---

## US-166: 모니터링 가이드

### 내용
1. **Grafana** (localhost:3001): 대시보드 접속법, 주요 패널 설명
2. **Engine API** (localhost:8000): `/api/v1/shadow/stats`, `/api/v1/health`
3. **Dashboard** (localhost:3000): 4페이지 설명, 실시간 WS 데이터
4. **CLI 모니터링**: `docker compose logs -f engine`, Redis 상태 확인
5. **Telegram 알림**: 거래 알림 + 워크플로우 알림 설정

---

## 5-Stage 실행 계획

### Stage A (기획)
- Entry Gate: SSOT/prd.json/CLAUDE.md 3-way 정합성 + Phase S8 US 추가
- PLAN.md: 이 문서 기반 `docs/planning/Phase-S8_PLAN.md`
- QUANT GATE: US-164(PnL 방어)만 퀀트 검증 필요

### Stage B (개발)
- TeamCreate("leviathan-phase-S8")
- 배치 1~4 순차 구현 (Yujin: engine, Wonyoung: tests)
- pytest PASS 후 TeamDelete

### Stage C (검증)
- 코드리뷰 + 보안리뷰 (config 파일 보안 확인)
- CRITICAL/HIGH 0건 확인

### Stage D (Shadow 10min+)
- 10개 거래소 연결 확인 (okx_futures, bybit_futures 포함)
- PnL > 0, crash = 0

### Stage E (정합성)
- SSOT.md §7에 Phase S8 추가, §9 업데이트
- prd.json US-157~166 passes:true
- git commit + push
- 텔레그램 → 사장님 승인

---

## Phase S8 완료 후: TF Final [단계 2] 계속

Phase S8 승인 후 즉시 TF Final [단계 2] Progressive Shadow 재개:
- Stage 2: 2H → 승률/PnL 추세 안정성 (WR>50%, PnL 양수)
- 10개 거래소 전체 연결, trading.json 기반 설정

### 사장님 모니터링 방법 (2H Shadow 중)

**1. 웹 대시보드** (http://localhost:3000)
- Overview: 실시간 PnL, 승률, 활성 거래
- Strategies: 전략별 성과
- Portfolio: 거래소별 잔고

**2. Grafana** (http://localhost:3001, admin/leviathan)
- Engine Dashboard: 전략 성과, 거래소 지연시간
- System Dashboard: CPU/메모리/네트워크

**3. Engine API** (http://localhost:8000)
- GET /api/v1/health — 엔진 상태
- GET /api/v1/shadow/stats — Shadow 통계
- GET /api/v1/shadow/recent-trades — 최근 거래

**4. CLI**
```bash
# 실시간 로그
docker compose logs -f engine

# Shadow 통계 (1분 간격)
watch -n 60 'curl -s localhost:8000/api/v1/shadow/stats | python3 -m json.tool'

# Docker 상태
docker compose ps
```

**5. Telegram**
- 거래 알림: 실시간 체결/PnL
- 워크플로우 알림: Phase 완료, 에러 발생 시

---

## 산출물 체크리스트

- [ ] `engine/config/trading.json` 신규 생성
- [ ] `engine/src/core/config.py` JSON 로딩 추가
- [ ] `.env` 비민감 항목 주석 처리 (하위호환)
- [ ] okx_futures/bybit_futures 활성화 (trading.json)
- [ ] Dashboard 로그인 수정
- [ ] 모니터링 가이드 문서 작성
- [ ] SSOT.md §7 Phase S8 추가, §9 업데이트
- [ ] prd.json US-157~166 추가
- [ ] CLAUDE.md 동기화
- [ ] pytest 0 failures
- [ ] Shadow 10min+ PASS
