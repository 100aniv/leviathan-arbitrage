# Phase S3 Infrastructure Hardening — Code Review

**Reviewers**: Jennie (code-reviewer/opus), Lisa (critic/opus), Rose (quality-reviewer), Jisoo (security-reviewer)
**Date**: 2026-03-14
**Files Reviewed**: 11 source + 8 config + 3 test
**Total Issues**: 12 (4 CRITICAL, 3 HIGH, 3 MEDIUM, 2 LOW — all resolved in Stage B fix loop)

## Scope

| US | Title | Files |
|----|-------|-------|
| US-135 | DB 스키마 통합 + 자동 마이그레이션 | `docker/init.sql`, `migration_runner.py`, `timescale.py` |
| US-136 | .env 동기화 검사기 | `preflight.py`, `main.py` |
| US-137 | DB 백업 restart policy | `docker-compose.yml` |
| US-138 | Alertmanager Telegram webhook | `alertmanager.yml`, `docker-compose.yml`, `prometheus.yml` |
| US-139 | Grafana 데이터소스 프로비저닝 | `datasources.yml`, `datasource.yml` |

---

## Stage 1: Spec Compliance

| US | Verdict | Notes |
|----|---------|-------|
| US-135 | PASS | docker/init.sql 통합 DDL (16 tables/views), migration_runner with advisory lock + transaction, schema_version tracking |
| US-136 | PASS | _check_env_sync() 4 keys 검사, main.py startup 호출, sensitive masking |
| US-137 | PASS | db-backup/wal-backup restart:"no" (one-shot scripts 무한 재시작 방지) |
| US-138 | PASS | alertmanager v0.27.0, sed-based env var substitution, WORKFLOW_TELEGRAM_BOT_TOKEN 분리 |
| US-139 | PASS | Grafana datasource provisioning, Prometheus alertmanager target |

---

## Stage 2: Issues Found & Resolved

### CRITICAL (4건 — 모두 해결)

**C-1. init.sql materialized views 컬럼 불일치**
- `timestamp`→`ts`, `pnl`→`net_pnl`, `exchange_buy`→`buy_exchange`, `pair`→`symbol`
- execution_log 테이블에 존재하지 않는 컬럼 참조 → CREATE MATERIALIZED VIEW 실패
- **Fix**: 3개 materialized view (strategy_daily_pnl, exchange_daily_pnl, pair_daily_pnl) 컬럼명 수정

**C-2. attribution.py 동일 컬럼 불일치**
- init.sql과 동일한 컬럼 매핑 오류
- **Fix**: migration_sql() 메서드 내 동일 수정 적용

**C-3. Alertmanager ${ENV_VAR} 미지원**
- alertmanager v0.27은 config 파일 내 ${ENV_VAR} 네이티브 치환 미지원
- 봇 토큰이 리터럴 `${WORKFLOW_TELEGRAM_BOT_TOKEN}`으로 전송됨
- **Fix**: sed-based Docker entrypoint로 placeholder 치환 방식 전환

**C-4. migration_runner advisory lock 부재**
- 다중 엔진 인스턴스 동시 기동 시 race condition
- **Fix**: pg_advisory_lock(73318) + transaction 래핑 추가

### HIGH (3건 — 모두 해결)

**H-1. migration_runner Docker 경로 불일치**
- `Path(__file__).parents[4]` 상대 경로가 Docker 컨테이너 내부에서 실패
- **Fix**: `_find_init_sql()` 다중 경로 탐색 + Docker volume mount (`./docker/init.sql:/app/docker/init.sql:ro`)

**H-2. _check_env_sync() dead code**
- preflight.py에 정의되었으나 어디서도 호출되지 않음
- **Fix**: main.py startup에서 호출 추가

**H-3. 백업 컨테이너 무한 재시작**
- db-backup/wal-backup에 `restart: unless-stopped` → one-shot 스크립트가 완료 후 무한 재시작
- **Fix**: `restart: "no"` 로 변경

### MEDIUM (3건 — 모두 해결)

**M-1. 민감 값 로그 노출**
- _check_env_sync() 불일치 시 REDIS_PASSWORD 등 실제 값이 warning 로그에 출력
- **Fix**: `_SENSITIVE_KEYS` frozenset + `_mask()` 헬퍼로 마스킹

**M-2. test mock 불완전**
- migration_runner에 transaction 추가 후 기존 mock이 conn.transaction() 미지원
- **Fix**: AsyncMock __aenter__/__aexit__ 추가

**M-3. _find_init_sql patch 불일치**
- INIT_SQL 상수 → _find_init_sql() 함수 변경 후 테스트 patch 미갱신
- **Fix**: patch target 업데이트

### LOW (2건)

**L-1. datasources.yml 중복** — `provisioning/datasources.yml`과 `provisioning/datasources/datasource.yml` 양쪽 존재. Grafana 자동 감지로 무해.

**L-2. Bitget WebSocket 주기적 재연결** — 2분 간격 `no close frame` 경고. 자동 재연결 작동 중. 기존 이슈.

---

## Stage 3: Security Review (Jisoo)

| 항목 | 판정 | 비고 |
|------|------|------|
| Secrets in config | PASS | alertmanager.yml에 placeholder만 커밋, 실제 토큰은 Docker env에서 주입 |
| Secrets in logs | PASS | _SENSITIVE_KEYS 마스킹 적용 |
| SQL injection | PASS | migration_runner는 init.sql 파일 전체 실행 (사용자 입력 없음) |
| Advisory lock | PASS | pg_advisory_lock(73318) 정상 해제 (finally 블록) |
| Token separation | PASS | WORKFLOW_TELEGRAM_BOT_TOKEN ≠ TELEGRAM_BOT_TOKEN (거래 알림 분리) |

---

## Stage 4: Quality Review (Rose)

| 항목 | 판정 | 비고 |
|------|------|------|
| SOLID compliance | PASS | migration_runner SRP 준수, _find_init_sql 분리 |
| Error handling | PASS | init.sql 미발견 시 warning + graceful return |
| Idempotency | PASS | IF NOT EXISTS + schema_version 체크 |
| Test coverage | PASS | 19 S3-specific tests (migration 5 + env_sync 6 + infra_yaml 8) |
| Anti-patterns | PASS | 이중 슬리피지 방지 유지 (POWERLAW_SLIPPAGE_K 분리) |

---

## Verdict

**PASS** — 모든 CRITICAL/HIGH 이슈 Stage B fix loop에서 해결됨. 보안/품질 검증 통과.
