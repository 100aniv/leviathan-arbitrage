# Phase S22 — Shadow trades=0 회귀 수정 + 보안 강화

**Status**: PENDING
**Date**: 2026-03-22
**Dependencies**: S21 완료 (US-297~300 passes:true)

---

## Phase 개요

Phase S22는 TF QF 9차 FAIL에서 발생한 회귀 5건(US-316~320)을 수정하는 긴급 회귀 Phase다.

- US-317, US-318, US-320은 이미 코드 PASS (런타임 검증 필요)
- US-316은 코드 PASS이나 Shadow 10min 런타임 검증 미완
- US-319만 구현 미완 (보안 HIGH: DB 비밀번호 하드코딩 + login rate limiter 누락)

배치 구조:
- **B-Step 1**: US-319 구현 (docker-compose.yml 변수화 + LoginRateLimitMiddleware)
- **B-Step 2**: Shadow 10min 실행 (US-316 런타임 검증)
- **B-Step 3**: Fix loop (B-Step 2 실패 시에만)
- **Stage C**: Assembly Gate → 코드리뷰 + 보안 → Go/No-Go → SSOT + git push

---

## US 목록

### US-316: Shadow trades=0 블로커 전수 조사 + 파라미터 튜닝 (코드 PASS → 런타임 검증 필요)

**목표**: SignalGenerator→Strategy→ShadowTrade 전체 파이프라인에서 10분 Shadow
실행 시 cross_exchange 전략 최소 1건 체결 확인.

**현황**:
- 코드 수정 완료 (REGIME_MIN_EDGE 5→2bps, ScheduledTuner override, config 통합)
- Shadow 10min 런타임 검증이 아직 수행되지 않음 — B-Step 2에서 실시

**수용 기준**:
- Shadow 10min: trades > 0 (cross_exchange 최소 1건)
- 전략 on_signal() 거부 로그에서 100% 거부율 전략 0개
- PnL > 0, crash 0건

---

### US-317: PASS (작업 없음)

---

### US-318: PASS (작업 없음)

---

### US-319: DB 하드코딩 비밀번호 제거 + login rate limiter 적용

**목표**: docker-compose.yml TimescaleDB 비밀번호 하드코딩 제거 및 /api/auth/login
엔드포인트에 brute-force 방어 rate limiter 적용.

**현황 분석 (코드베이스 조사 결과)**:

docker-compose.yml의 하드코딩 위치 (총 3곳):
- L131: `timescaledb` 서비스 `POSTGRES_PASSWORD: leviathan` — 필수 변수화
- L387: `db-backup` 서비스 `PGPASSWORD: leviathan` — 필수 변수화
- L463: `wal-backup` 서비스 `PGPASSWORD: leviathan` — 필수 변수화

추가 확인:
- L39: `DATABASE_URL: postgresql+asyncpg://leviathan:leviathan@timescaledb:5432/leviathan`
  (engine 서비스) — URL에 비밀번호 포함, env var 치환 필요
- L330, L357: bot-gateway, auto-tuner 서비스 동일 DATABASE_URL 하드코딩

로그인 endpoint: `/api/auth/login` (`server.py` L116)
- 현재 `RateLimitMiddleware`는 `/api/v1/*` 경로만 감시 (`middleware.py` L27: `_API_PREFIX = "/api/v1/"`)
- `/api/auth/login`은 `/api/v1/` 외부이므로 rate limit 미적용
- `LoginRateLimitMiddleware` 미존재 — 신규 구현 필요

root `.env` 현황:
- `POSTGRES_PASSWORD` 키 없음 — 추가 필요
- `DATABASE_URL=postgresql+asyncpg://leviathan:leviathan@localhost:5432/leviathan` — URL 내 비밀번호 노출

**구현 계획**:

**Task 1**: `docker-compose.yml` POSTGRES_PASSWORD 변수화 (3곳 + DATABASE_URL 5곳)
- `POSTGRES_PASSWORD: leviathan` → `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}`
- `PGPASSWORD: leviathan` (db-backup) → `PGPASSWORD: ${POSTGRES_PASSWORD:?}`
- `PGPASSWORD: leviathan` (wal-backup) → `PGPASSWORD: ${POSTGRES_PASSWORD:?}`
- `DATABASE_URL: postgresql+asyncpg://leviathan:leviathan@...` (engine, bot-gateway, auto-tuner)
  → `DATABASE_URL: postgresql+asyncpg://leviathan:${POSTGRES_PASSWORD:?}@timescaledb:5432/leviathan`
- `pg_isready` healthcheck은 비밀번호 불필요 — 변경 없음

**Task 2**: root `.env`에 `POSTGRES_PASSWORD` 추가 + `engine/.env` 동기화
- root `.env`: `POSTGRES_PASSWORD=leviathan` 추가 (기존 값 보존, 환경별 교체 용이)
- `engine/.env`: DATABASE_URL 내 비밀번호 → env var 치환 또는 주석으로 동기화 노트 추가

**Task 3**: `LoginRateLimitMiddleware` 구현 (`engine/src/api/middleware.py` 추가)
- 경로: `/api/auth/` prefix 감시
- 한도: 5 req/min/IP (sliding window, 기존 `RateLimitMiddleware` 패턴 재사용)
- 응답: HTTP 429, `{"detail": "Too Many Requests — login rate limit exceeded"}`,
  `Retry-After: 60` 헤더
- `server.py` `create_app()` 내 `app.add_middleware(LoginRateLimitMiddleware)` 등록

**Task 4**: 단위 테스트 (`engine/tests/`)
- `tests/unit/api/test_login_rate_limiter.py`
  - 5 req/min 이내: HTTP 200 반환
  - 6번째 req: HTTP 429 반환
  - 60초 윈도우 경과 후: 다시 허용
- `tests/unit/infra/test_docker_compose_env.py` (또는 기존 infra 테스트에 추가)
  - docker-compose.yml 파싱 후 `POSTGRES_PASSWORD: leviathan` 리터럴 없음 검증
  - `${POSTGRES_PASSWORD` 패턴 존재 검증

**파일 변경**:
- `docker-compose.yml` — 하드코딩 비밀번호 8곳 변수화
- `.env` (root) — `POSTGRES_PASSWORD=leviathan` 추가
- `engine/.env` — 동기화 노트 추가 (실제 URL은 로컬 직접 연결이므로 유지)
- `engine/src/api/middleware.py` — `LoginRateLimitMiddleware` 클래스 추가
- `engine/src/api/server.py` — `LoginRateLimitMiddleware` import + `add_middleware` 등록
- `engine/tests/unit/api/test_login_rate_limiter.py` — 신규 테스트 파일

**WIRING AC (US-319)**:
- AC1 (생성): `middleware.py`에 `LoginRateLimitMiddleware` 클래스 존재 (path prefix: `/api/auth/`)
- AC2 (주입): `server.py` `create_app()` 내 `app.add_middleware(LoginRateLimitMiddleware)` 호출
- AC3 (호출): 6번째 `/api/auth/login` 요청 시 HTTP 429 반환 — 단위 테스트 + 런타임 로그 증거

**수용 기준**:
- `docker-compose.yml`에 `POSTGRES_PASSWORD: leviathan` 리터럴 0건
- `docker-compose.yml`에 `PGPASSWORD: leviathan` 리터럴 0건
- `docker-compose.yml`에 `DATABASE_URL: postgresql+asyncpg://leviathan:leviathan` 리터럴 0건
- `/api/auth/login` 6번째 요청 → HTTP 429 (단위 테스트 증거)
- root `.env`에 `POSTGRES_PASSWORD` 키 존재
- 기존 미들웨어 단위 테스트 전원 통과

---

### US-320: PASS (작업 없음)

---

## 배치 구조

```
Stage B
├── B-Step 1 (순차): US-319 구현
│   ├── Task 1: docker-compose.yml 변수화 (8곳)
│   ├── Task 2: root .env + engine/.env 동기화
│   ├── Task 3: LoginRateLimitMiddleware 구현 + server.py 등록
│   └── Task 4: 단위 테스트 작성 + 전체 테스트 통과 확인
│
└── B-Step 2 (순차 — B-Step 1 완료 후): Shadow 10min 실행
    ├── 대상: US-316 런타임 검증
    ├── 명령: cd engine && timeout 600 python -m src.main
    ├── 성공 기준: trades > 0, PnL > 0, crash 0건
    └── 실패 시: B-Step 3 (Fix loop) 진입

B-Step 3 (조건부 — B-Step 2 실패 시에만):
└── Fix loop: debugger → parameter 재조정 → Shadow 재실행
    ├── Type P: 파라미터 튜닝 (최대 3회)
    └── Type W: Wiring 수정 → L2 에스컬레이션

Stage C (B-Step 2 PASS 후)
├── C-Step 1: Assembly Gate
│   ├── 대상: US-319에서 class 신규 추가 (LoginRateLimitMiddleware) — 조립 검증 필수
│   ├── 검증 항목: init chain, middleware 등록 순서, dead wiring 점검
│   └── PASS 후에만 C-Step 2 진행
│
├── C-Step 2 (병렬): 코드리뷰 + 보안 + 멀티모델 감사
│   ├── Jennie (code-reviewer/opus): middleware 패턴, sliding window 정확성
│   ├── Jisoo (security-reviewer): rate limiter bypass 가능성, env var 노출 경로
│   └── 멀티모델 CLI (codex/gemini): docker-compose env var 패턴 감사
│   └── quorum 2+ 지적 = MUST FIX
│
├── C-Step 3: Go/No-Go
│   └── 체크리스트:
│       - [ ] US-316 Shadow trades > 0 (런타임 로그 증거)
│       - [ ] US-319 docker-compose.yml 리터럴 0건
│       - [ ] US-319 LoginRateLimitMiddleware HTTP 429 동작
│       - [ ] 전체 테스트 5,192+ passed, 0 failed
│       - [ ] Assembly Gate PASS
│       - [ ] 코드리뷰 MUST FIX 0건
│
└── C-Step 4: SSOT + git push
    ├── prd.json US-316/319 passes:true 업데이트
    ├── SSOT.md Phase S22 완료 기록
    ├── python -m src.workflow.cli sync ...
    └── git push → TF QF 10차 자동 진입
```

---

## 의존성 + 병렬성

| 단계 | 의존성 | 병렬 가능 |
|------|--------|---------|
| B-Step 1 Task 1~2 | 없음 | Task 1 + Task 2 병렬 가능 |
| B-Step 1 Task 3 | Task 1~2 완료 불필요 (독립) | 병렬 가능 |
| B-Step 1 Task 4 | Task 3 완료 후 | 순차 |
| B-Step 2 | B-Step 1 완료 후 | 순차 |
| B-Step 3 | B-Step 2 FAIL 시 | 조건부 |
| C-Step 1 | B-Step 2 PASS 후 | 순차 |
| C-Step 2 | C-Step 1 PASS 후 | 내부 병렬 |
| C-Step 3 | C-Step 2 완료 후 | 순차 |
| C-Step 4 | C-Step 3 GO 후 | 순차 |

---

## 예상 테스트 수

| US | 신규 | 기존 유지 | 비고 |
|----|------|----------|------|
| US-319 | +6~8 | middleware 전체 | login rate limiter 3개 + docker-compose 검증 3개 |
| US-316 | 0 | 기존 전체 | 런타임 검증만 |
| **총계** | **+6~8** | | |

현재 5,192 tests → 예상 **5,198~5,200 tests**

---

## 리스크 + 완화 전략

| 리스크 | 가능성 | 완화 |
|-------|--------|------|
| `POSTGRES_PASSWORD:?` 문법으로 docker compose up 실패 | 중 | root .env에 반드시 POSTGRES_PASSWORD 추가 후 변경. `docker compose config` 로 검증 |
| LoginRateLimitMiddleware 순서 충돌 (IPWhitelistMiddleware와) | 저 | Starlette middleware 스택은 역순 적용 — `add_middleware` 순서 주석 명시 |
| Shadow 10min trades=0 재발 | 중 | S22 코드 수정(REGIME_MIN_EDGE 2bps) 검증이 목적. 실패 시 B-Step 3 Type P (3회) |
| engine/.env DATABASE_URL 내 평문 비밀번호 | 저 | 로컬 직접 연결용 — .gitignore 확인 후 현행 유지, 주석으로 동기화 주의사항 명시 |

---

## Shadow 실행 기준 (Phase S22 완료 조건)

단위 테스트 통과만으로 Phase 완료 선언 금지.

1. US-316: Shadow 10min — trades > 0, PnL > 0, crash 0건 (로그 증거)
2. US-319: HTTP 429 단위 테스트 PASS + docker-compose.yml 리터럴 검증 PASS
3. prd.json US-316, US-319 passes:true 업데이트
4. SSOT.md Phase S22 완료 기록 + git push
5. TF QF 10차 자동 진입

---

## 담당 팀

- **B-Step 1**: IVE (executor) — US-319 구현
- **B-Step 2**: NewJeans / Minji (shadow-tester) — Shadow 10min 실행
- **B-Step 3**: Fix 루프 (Joy/debugger) — 조건부
- **C-Step 1**: Assembly Verifier (verifier/sonnet)
- **C-Step 2**: BLACKPINK (Jennie + Jisoo) + 멀티모델 CLI
- **C-Step 4**: LE SSERAFIM / Sakura (ssot-keeper)
