# Phase F: Live 전환 준비 — PLAN.md

> **작성일**: 2026-03-29 | **Phase**: F (Live Preparation)
> **선행 조건**: SIT-3 PASS + TF QF 12차 PASS + TF SF CONDITIONAL PASS
> **목적**: Live 전환 시 발견된 5개 근본 문제 해결 → 실 자금 투입 가능 상태

---

## Entry Gate 정합성 (3-Way)

| 항목 | SSOT.md | CLAUDE.md | prd.json | 상태 |
|------|---------|-----------|----------|------|
| PRD total | 343 | 343 | 343 | OK |
| passes:true | 338 | 338 | 338 | OK |
| passes:false | 5 (US-055/056/332/334/339) | 5 | 5 | OK |
| Tests | 5,252 | 5,241 | N/A | **불일치** |
| Current Phase | SIT-3 | SIT-3 | - | OK |

**수정 필요**: CLAUDE.md Tests 5,241 → 5,252 동기화 (Stage C에서 처리)

---

## Context (탐색 기반 근거)

| # | 문제 | 코드 위치 | 근본 원인 |
|---|------|-----------|-----------|
| 1 | LiveGate 재시작 차단 | `live_gate.py:81` EVALUATION_DAYS=7 | WalkForward 7일 데이터 필요 → 재시작 시 0에서 시작 |
| 2 | 모드 전환 미동작 | `settings.py:113` | `ctx.execution_mode` 문자열만 변경, adapter 미전환 |
| 3 | PnL 세션 혼합 | `main.py:2476-2492` | warm-start가 모드 무관 PnL 복원 |
| 4 | 전략-거래소 매핑 없음 | 전체 codebase | `required_exchanges` 개념 부재 |
| 5 | 대시보드 UX 미흡 | dashboard 전반 | 모드/전략/KPI 표시 불충분 |

---

## US-F01: LiveGate 재설계

> **목표**: 엔진 재시작 시 이전 LiveGate PASS 상태 유지 → 즉시 Live 가능

### Acceptance Criteria

1. LiveGate PASS 상태를 DB(`engine_state` 테이블)에 저장. 재시작 시 복원하여 재평가 없이 Live 진입 가능
2. LiveGate가 PASS→FAIL 전환 시 텔레그램 경고 + 자동 Shadow fallback (기존 `enforce_or_fallback` 유지)
3. `LIVE_GATE_BYPASS=true` env var로 소액 테스트 시 게이트 우회 가능 (텔레그램 경고 발송)
4. ContinuousLiveGateMonitor(`live_gate_continuous.py`)가 5분 주기 재평가 유지 → FAIL 시 halt

### 수정 파일

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `src/modes/live_gate.py` | :81-111 | `__init__`에 DB pool로 이전 PASS 상태 복원 로직 추가 |
| `src/modes/live_gate.py` | :387-409 | `enforce_or_fallback()`에 DB 저장된 PASS 상태 체크 분기 |
| `src/main.py` | :2494-2510 | LiveGate 초기화 시 DB에서 last_pass_timestamp 로드 |
| `src/core/config.py` | LiveGateSettings | `bypass_enabled` 필드 추가 |
| `.env` | - | `LIVE_GATE_BYPASS=false` 기본값 |

### Trade-offs

| 옵션 | 장점 | 단점 |
|------|------|------|
| A: DB 상태 저장 (채택) | 안전 + 재시작 복원 | DB 의존성 |
| B: 경고 전용 | 운영 유연 | 게이트 무력화 위험 |
| C: 평가기간 단축 | 빠른 재진입 | 통계 신뢰도 저하 |

---

## US-F02: 대시보드 모드 전환

> **목표**: 대시보드에서 모드 전환 시 실제 엔진 동작 반영

### Acceptance Criteria

1. `PATCH /api/v1/settings/mode` 호출 시 `ctx.execution_mode` + `DATA_MODE` env 동기화
2. Live 전환 요청 → LiveGate 체크 → PASS 시 엔진 자동 재시작 트리거 (graceful restart)
3. 대시보드 UI에 "재시작 진행중" 상태 표시 + 완료 시 자동 리프레시
4. Shadow→Paper 전환은 런타임 가능 (adapter 변경 불필요). Live는 재시작 필수

### 수정 파일

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `src/api/routes/settings.py` | :74-117 | Live 전환 시 graceful restart 트리거 (SIGHUP or subprocess) |
| `src/main.py` | :2040-2060 | SIGHUP 핸들러 추가 → mode 변경 후 재시작 |
| `dashboard/src/app/settings/page.tsx` | - | 모드 전환 버튼에 재시작 확인 다이얼로그 + 로딩 상태 |

### Trade-offs

| 옵션 | 장점 | 단점 |
|------|------|------|
| A: 엔진 재시작 (채택) | 안전, adapter 확실 전환 | 2-5초 다운타임 |
| B: 런타임 adapter 교체 | UX 최고 | 복잡, race condition 위험 |
| C: 수동 재시작 가이드 | 구현 최소 | UX 최악 |

---

## US-F03: PnL 세션 관리

> **목표**: Shadow/Paper/Live PnL 분리 + 세션별 추적

### Acceptance Criteria

1. 엔진 시작 시 `session_id` (UUID) 생성, 모든 거래에 태깅
2. warm-start 복원 시 동일 모드의 PnL만 복원 (Shadow→Shadow OK, Shadow→Live 복원 금지)
3. `GET /api/v1/shadow/stats`에 `session_pnl` + `cumulative_pnl` 구분 반환
4. 대시보드 PnL 차트에 "세션" / "누적" 토글 버튼
5. 모드 전환 시 PnL 자동 리셋 (새 세션 시작)

### 수정 파일

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `src/modes/shadow.py` | ShadowStats dataclass | `session_id`, `session_start`, `mode` 필드 추가 |
| `src/main.py` | :2476-2492 | warm-start에 mode 필터 조건 추가 |
| `src/api/routes/portfolio.py` | - | 세션/누적 PnL 구분 API |
| `dashboard/src/components/` | PnLChart | 세션/누적 토글 UI |

### Trade-offs

| 옵션 | 장점 | 단점 |
|------|------|------|
| 세션 ID 방식 (채택) | 완전 분리, 감사 추적 가능 | 스키마 변경 필요 |
| 타임스탬프 기반 | 스키마 변경 없음 | 경계 모호 |

---

## US-F04: 전략-거래소 매핑

> **목표**: 전략별 필요 거래소 정의 → 불가능 전략 자동 비활성

### Acceptance Criteria

1. `config/trading.json`에 전략별 `required_exchanges` 필드 추가 (예: cross_exchange → 2+거래소)
2. 엔진 시작 시 활성 거래소와 매칭 → 불충족 전략 자동 비활성 + 로그 경고
3. 대시보드 Settings에서 거래소 선택 시 가능/불가능 전략 실시간 표시
4. API `GET /api/v1/settings`에 `strategy_availability` 필드 추가

### 수정 파일

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `config/trading.json` | strategies 섹션 | `required_exchanges` 매핑 추가 |
| `src/main.py` | `_init_strategies()` | 거래소 체크 로직 추가 |
| `src/api/routes/settings.py` | :31-47 | `strategy_availability` 반환 |
| `dashboard/src/app/settings/page.tsx` | - | 전략-거래소 연동 UI |

### 전략-거래소 매핑 정의

| 전략 | 필요 조건 | 비고 |
|------|-----------|------|
| cross_exchange | spot 거래소 2개 이상 | 교차 거래소 아비트라지 |
| spot_futures | 1 spot + 1 futures (동일 거래소) | Binance/OKX/Bybit |
| futures_futures | futures 거래소 2개 이상 | OKX/Bybit/Binance Futures |
| triangular | 3+ KRW 페어 보유 거래소 1개 | Upbit/Bithumb/Coinone |
| funding_rate | futures 거래소 1개 이상 | 펀딩레이트 수집 가능 |
| statistical_arb | 거래소 2개 이상 (spot or futures) | 상관관계 분석 |
| cex_dex | CEX 1개 + DEX 연동 | 미구현 (비활성) |

---

## US-F05: 대시보드 UX 종합

> **목표**: 오버뷰 KPI + 전략 설명 + 모드 구분 UX 개선

### Acceptance Criteria

1. 오버뷰 페이지에 핵심 KPI 4개 상단 배치: 총 PnL, 오늘 PnL, 활성 전략 수, 현재 모드
2. 전략 카드에 한글 역할 설명 + 필요 거래소 목록 표시
3. 현재 모드 뱃지 (SHADOW=파란색, PAPER=노란색, LIVE=빨간색) 헤더 고정
4. Shadow 데이터 vs Live 데이터 시각적 구분 (Shadow: 점선 테두리, Live: 실선)
5. 모드 전환 버튼에 LiveGate 상태 인라인 표시 (PASS/FAIL + 6-check 요약)

### 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `dashboard/src/app/page.tsx` | 오버뷰 KPI 4개 위젯 추가 |
| `dashboard/src/components/StrategyPanel.tsx` | 전략 한글 설명 + required_exchanges |
| `dashboard/src/app/layout.tsx` | 헤더에 모드 뱃지 고정 |
| `dashboard/src/app/settings/page.tsx` | LiveGate 상태 인라인 + 모드 전환 개선 |

---

## 수행 순서

```
Stage A (기획) ← 현재 단계
  └─ Entry Gate ✅ + PLAN.md 작성 ✅

Stage B (구현 + 검증)
  ├─ B-Step 1: TeamCreate → US-F01~F05 병렬 구현
  │   ├─ IVE팀: Yujin(F01-LiveGate), Gaeul(F02-모드전환), Leeseo(F03-PnL세션)
  │   ├─ IVE팀: Liz(F04-전략매핑), Rei(F05-대시보드UX)
  │   └─ Wonyoung: 각 US별 단위테스트
  ├─ B-Step 2: Shadow 10min (13항목 복합지표)
  │   └─ NewJeans팀: Minji(shadow), Hanni(QA), Danielle(분석)
  └─ pytest 전체 통과 확인

Stage C (리뷰 + 릴리스)
  ├─ C-Step 1: Assembly Gate (init chain + signal flow + config audit)
  ├─ C-Step 2: BLACKPINK 코드리뷰 + AI CLI 교차검증
  ├─ C-Step 3: Go/No-Go (LE SSERAFIM)
  └─ C-Step 4: SSOT 동기화 + git push
```

## Live 전환 전 종합 체크리스트 (Part 6)

- [ ] US-F01: LiveGate 재시작 시 즉시 Live 가능
- [ ] US-F02: 대시보드 모드 전환 실동작
- [ ] US-F03: PnL 세션 분리 (Shadow/Live 혼합 없음)
- [ ] US-F04: 전략-거래소 매핑 동작
- [ ] US-F05: 대시보드 UX 개선 완료
- [ ] Binance API 키 잔고 조회 성공
- [ ] 거래소당 포지션 한도 $50 설정
- [ ] 텔레그램 3봇 정상
- [ ] Shadow 10min PASS (13항목)
- [ ] SSOT.md 동기화 완료
