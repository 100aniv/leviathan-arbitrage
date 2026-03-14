# Phase S3: Infrastructure Hardening — PLAN.md

> **Phase**: S3 (TF Semi-Final 회귀)
> **대상 US**: US-135, US-136, US-137, US-138, US-139
> **도메인**: 인프라 (DB, .env, Nginx, Alertmanager, Docker)
> **생성일**: 2026-03-14
> **작성자**: Planner (AESPA/Giselle)

---

## 1. Context

Phase S2 (Engine Wiring) 완료 후, TF Semi-Final에서 발견된 인프라 결함 5건을 수정한다.
모든 US가 인프라 도메인이므로 단일 배치로 처리 가능하나, `docker-compose.yml`이
US-135/137/138/139에서 공통 수정 대상이므로 충돌 방지를 위해 순서 제어가 필요하다.

### 현재 문제점 요약

| US | 핵심 결함 | 심각도 |
|----|---------|--------|
| US-135 | DB 스키마 3중 분산 (timescale.py / migrations/ / attribution.py), docker/init.sql 미존재, 마이그레이션 러너 없음 | CRITICAL |
| US-136 | root .env MIN_EDGE_BPS=5 vs engine/.env=3 불일치, main.py 기본값 40, SLIPPAGE_K_DEFAULT=1.0 (0.0이어야 함) | HIGH |
| US-137 | Nginx WS proxy_pass engine:8000 (엔진 WS는 8001), db-backup/wal-backup restart:"no" | HIGH |
| US-138 | Alertmanager 미존재 (주석 처리), Grafana datasource 2중 파일 (timeInterval 불일치) | HIGH |
| US-139 | Redis/TimescaleDB mem_limit 미설정, Promtail healthcheck 없음 | MEDIUM |

---

## 2. Work Objectives

1. DB 스키마를 단일 소스(`docker/init.sql`)로 통합하고, 엔진 시작 시 자동 마이그레이션 적용
2. `.env` 파일 간 불일치를 해소하고, preflight 검증 추가
3. Nginx WebSocket 포트를 올바르게 매핑하고, 백업 컨테이너 자동 재시작 설정
4. Alertmanager 컨테이너 추가 + Prometheus 연결 + Grafana datasource 정리
5. Docker 리소스 제한 + 누락된 healthcheck 추가

---

## 3. Guardrails

### Must Have
- `docker compose up` 한 번으로 전체 스키마 + 마이그레이션 자동 적용
- 기존 4,346 테스트 전부 PASS 유지
- Shadow 10min 실행 시 crash=0, PnL>0
- docker-compose.yml 변경 후 `docker compose config --quiet` 검증 PASS

### Must NOT Have
- 기존 테이블 DROP 또는 데이터 손실
- PowerLawSlippage k>0 (이중 슬리피지 금지)
- 하드코딩된 비밀번호 (env var 참조 유지)
- Alertmanager에 실 Telegram 봇 토큰 커밋 (env var 참조)

---

## 4. Task Flow (의존성 기반 실행 순서)

```
US-135 (DB 스키마 통합)  ──→  US-139 (Docker 리소스)
       │                              │
       ↓                              ↓
US-136 (.env 동기화)     ──→  US-137 (Nginx+백업) ──→ US-138 (Alertmanager)
  [독립 실행 가능]                                      [docker-compose.yml 최종 수정]
```

**실행 순서**: US-135 → US-136 (병렬 가능) → US-137 → US-139 → US-138 (최종)

**근거**:
- US-135가 docker-compose.yml에 init.sql 볼륨 마운트 추가 (기초)
- US-136은 docker-compose.yml 수정 없이 .env + Python 코드만 변경 (독립)
- US-137은 nginx.conf + docker-compose.yml restart 정책 수정
- US-139는 docker-compose.yml mem_limit + healthcheck 추가
- US-138이 docker-compose.yml에 alertmanager 서비스 추가 (최종, 가장 큰 변경)

---

## 5. Detailed TODOs

### US-135: DB 스키마 통합 + 자동 마이그레이션

**배정**: Yujin (executor)

**변경 파일**:
- `docker/init.sql` (신규 생성)
- `engine/src/infra/db/timescale.py` (리팩터링)
- `engine/src/infra/db/migration_runner.py` (신규 생성)
- `docker-compose.yml` (init.sql 볼륨 마운트)

**상세 작업**:

1. **`docker/init.sql` 생성** — 모든 DDL을 단일 파일로 통합:
   ```
   -- Extension
   CREATE EXTENSION IF NOT EXISTS timescaledb;

   -- From migrations/001_init_schema.sql
   orderbook_snapshots, execution_log, ohlcv_1m + hypertables + indexes

   -- From timescale.py
   ohlcv, spreads, signals + hypertables + retention policies + continuous aggregates (ohlcv_1m, ohlcv_1h)

   -- From migrations/002_tuning_logs.sql
   adaptive_threshold_log, regime_detector_log + hypertables

   -- From migrations/003_shadow_stage_results.sql
   shadow_stage_results

   -- From attribution.py migration_sql()
   strategy_daily_pnl, exchange_daily_pnl, pair_daily_pnl (materialized views)
   ```
   - 모든 DDL에 `IF NOT EXISTS` 사용 (멱등성 보장)
   - 주의: `ohlcv` (timescale.py)와 `ohlcv_1m` (001_init_schema.sql)은 별개 테이블 — 둘 다 유지

2. **`docker-compose.yml` 수정** — timescaledb 서비스에 init.sql 마운트:
   ```yaml
   volumes:
     - ./docker/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
   ```
   - 주의: `docker-entrypoint-initdb.d`는 DB가 처음 생성될 때만 실행됨
   - 기존 DB에는 migration_runner로 적용

3. **`engine/src/infra/db/migration_runner.py` 생성** — 엔진 시작 시 자동 마이그레이션:
   ```python
   async def run_migrations(conn):
       """Read docker/init.sql and execute missing DDL statements."""
       # schema_version 테이블로 적용 이력 관리
       # 각 migration 파일을 순서대로 실행 (IF NOT EXISTS로 안전)
   ```

4. **`engine/src/infra/db/timescale.py` 리팩터링**:
   - `setup_timescaledb()` → `migration_runner.run_migrations()` 호출로 위임
   - 기존 인라인 DDL 문자열 유지 (하위 호환), 단 migration_runner가 우선

5. **`engine/src/main.py` 수정** — `_init_infrastructure()`에서 migration_runner 호출

**Acceptance Criteria**:
- [ ] `docker/init.sql` 존재, 모든 테이블/뷰 DDL 포함
- [ ] `docker compose up` 시 fresh DB에 전체 스키마 자동 생성
- [ ] 엔진 시작 시 `run_migrations()` 호출, 기존 DB에 누락 테이블 생성
- [ ] `strategy_daily_pnl`, `exchange_daily_pnl`, `pair_daily_pnl` materialized views 생성 확인
- [ ] 기존 테스트 전부 PASS

---

### US-136: .env 동기화 + MIN_EDGE_BPS preflight

**배정**: Gaeul (executor)

**변경 파일**:
- `.env` (root)
- `engine/.env`
- `engine/src/main.py`
- `engine/src/modes/shadow.py`
- `engine/src/modes/preflight.py`

**상세 작업**:

1. **`.env` 파일 동기화**:
   - root `.env`: `MIN_EDGE_BPS=5` (현재 OK)
   - engine/.env: `MIN_EDGE_BPS=3` → `5`로 변경
   - 근거: CLAUDE.md 기준 MIN_EDGE_BPS=5가 확정값

2. **`engine/src/main.py` 기본값 수정**:
   - Line 488: `os.environ.get("MIN_EDGE_BPS", "40")` → `"5"`
   - 근거: .env 없이 실행해도 올바른 기본값 사용

3. **`engine/src/modes/shadow.py` PowerLawSlippage 기본값 수정**:
   - Line 78: `os.getenv("SLIPPAGE_K_DEFAULT", "5.0")` → `"0.0"`
   - 근거: k=0이 확정값 (SSOT.md $4.1 참조, SignalGenerator의 CEXOrderbookSlippage가 유일한 슬리피지 소스)
   - 주의: .env의 `SLIPPAGE_K_DEFAULT=1.0` → `0.0`으로도 변경

4. **`.env` 파일 SLIPPAGE_K_DEFAULT 동기화**:
   - root `.env`: `SLIPPAGE_K_DEFAULT=1.0` → `0.0`
   - engine/.env: `SLIPPAGE_K_DEFAULT=1.0` → `0.0`

5. **`engine/src/modes/preflight.py` 확장** — .env 동기화 preflight 체크 추가:
   ```python
   async def _check_env_sync(self) -> PreflightCheck:
       """Compare critical env vars between root .env and engine/.env."""
       # MIN_EDGE_BPS, SLIPPAGE_K_DEFAULT, REDIS_PASSWORD 비교
       # 불일치 시 WARNING 로그 + PreflightCheck(passed=False)
   ```

**Acceptance Criteria**:
- [ ] root .env와 engine/.env의 `MIN_EDGE_BPS` 값 일치 (=5)
- [ ] `main.py` 기본값이 `5` (40 아님)
- [ ] `PowerLawSlippage` 기본 k가 `0.0` (5.0 아님)
- [ ] `.env` 파일 SLIPPAGE_K_DEFAULT=0.0
- [ ] preflight 체크에서 두 .env 불일치 시 WARNING 로그 출력
- [ ] 기존 테스트 전부 PASS

---

### US-137: Nginx WS 포트 + 백업 자동재시작

**배정**: Leeseo (executor)

**변경 파일**:
- `infra/nginx/nginx.conf`
- `docker-compose.yml`

**상세 작업**:

1. **Nginx WebSocket 포트 확인 및 수정**:
   - 현재: `/ws` → `proxy_pass http://engine:8000` (REST 포트)
   - 현재: `/ws/feed` → `proxy_pass http://engine:8000` (REST 포트)
   - **조사 결과**: 엔진이 REST(8000)와 WS(8001)를 별도 포트로 서빙하는지, 또는 FastAPI 내에서 동일 포트(8000)로 WS를 핸들링하는지 확인 필요
   - docker-compose.yml에서 engine은 8000(REST+Prometheus), 8001(WS)을 노출
   - **수정**: `/ws` 및 `/ws/feed` location의 `proxy_pass`를 `http://engine:8001`로 변경
   - 단, 엔진 코드에서 WS가 8000에서도 서빙되는 경우 현행 유지 (코드 확인 필수)

2. **백업 컨테이너 restart 정책 수정**:
   - `db-backup`: `restart: "no"` → `restart: unless-stopped`
   - `wal-backup`: `restart: "no"` → `restart: unless-stopped`
   - 근거: 백업 스크립트가 일회성이 아닌 주기적 실행이어야 함
   - 주의: 현재 backup_db.sh는 pg_dump 후 종료됨 → cron/sleep 루프 또는 Docker restart로 주기적 실행
   - 대안: backup 스크립트에 `while true; sleep 86400; do ...` 루프 추가, 또는 그대로 `restart: unless-stopped`로 매일 재시작

3. **백업 healthcheck 추가** (db-backup, wal-backup):
   ```yaml
   healthcheck:
     test: ["CMD-SHELL", "test -f /backups/timescaledb/latest || true"]
     interval: 86400s
     timeout: 10s
     retries: 1
   ```

**Acceptance Criteria**:
- [ ] Nginx `/ws` 및 `/ws/feed`가 올바른 엔진 WS 포트로 프록시
- [ ] `db-backup`, `wal-backup`에 `restart: unless-stopped` 설정
- [ ] `docker compose up` 후 백업 컨테이너가 정상 동작
- [ ] 기존 nginx healthcheck PASS

---

### US-138: Alertmanager 연결 + Grafana datasource 정리

**배정**: Liz (executor)

**변경 파일**:
- `infra/prometheus/alertmanager.yml` (신규 생성)
- `infra/grafana/provisioning/datasources.yml` (수정)
- `infra/grafana/provisioning/datasources/datasource.yml` (제거 또는 통합)
- `infra/prometheus/prometheus.yml` (alerting 섹션 활성화)
- `docker-compose.yml` (alertmanager 서비스 추가)

**상세 작업**:

1. **Alertmanager 설정 파일 생성** (`infra/prometheus/alertmanager.yml`):
   ```yaml
   global:
     resolve_timeout: 5m
   route:
     receiver: 'telegram'
     group_by: ['alertname', 'severity']
     group_wait: 30s
     group_interval: 5m
     repeat_interval: 4h
     routes:
       - match: { severity: critical }
         receiver: 'telegram'
         repeat_interval: 1h
   receivers:
     - name: 'telegram'
       telegram_configs:
         - bot_token: '${WORKFLOW_TELEGRAM_BOT_TOKEN}'
           chat_id: ${WORKFLOW_TELEGRAM_CHAT_ID}
           parse_mode: 'HTML'
           message: |
             {{ range .Alerts }}
             <b>{{ .Labels.severity | toUpper }}</b>: {{ .Annotations.summary }}
             {{ .Annotations.description }}
             {{ end }}
   ```
   - env var 참조로 토큰 하드코딩 방지

2. **docker-compose.yml에 alertmanager 서비스 추가**:
   ```yaml
   alertmanager:
     image: prom/alertmanager:v0.27.0
     container_name: leviathan-alertmanager
     restart: unless-stopped
     ports:
       - "9093:9093"
     networks:
       - leviathan
     volumes:
       - ./infra/prometheus/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
     environment:
       - WORKFLOW_TELEGRAM_BOT_TOKEN=${WORKFLOW_TELEGRAM_BOT_TOKEN}
       - WORKFLOW_TELEGRAM_CHAT_ID=${WORKFLOW_TELEGRAM_CHAT_ID}
     healthcheck:
       test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:9093/-/healthy"]
       interval: 10s
       timeout: 5s
       retries: 5
       start_period: 10s
   ```

3. **Prometheus alerting 섹션 활성화** (`prometheus.yml`):
   ```yaml
   alerting:
     alertmanagers:
       - static_configs:
           - targets: ['alertmanager:9093']
   ```

4. **Grafana datasource 중복 제거**:
   - 현재 상태: `provisioning/datasources.yml` (Prometheus + Loki) vs `provisioning/datasources/datasource.yml` (Prometheus + TimescaleDB)
   - 문제: Prometheus가 중복 정의 (timeInterval 5s vs 15s 불일치)
   - 해결: `provisioning/datasources.yml` 제거, `provisioning/datasources/datasource.yml`에 3개 모두 통합:
     - Prometheus (timeInterval=15s, Alertmanager와 일치)
     - TimescaleDB
     - Loki

**Acceptance Criteria**:
- [ ] `docker compose up` 시 alertmanager 컨테이너 healthy
- [ ] Prometheus → Alertmanager 경로 활성 (`/-/healthy` 확인)
- [ ] Telegram receiver 설정 완료 (env var 참조)
- [ ] Grafana datasource 파일 단일화, 중복 제거
- [ ] Grafana UI에서 Prometheus + TimescaleDB + Loki 3개 datasource 확인

---

### US-139: Docker 리소스 제한 + 모니터링 healthcheck

**배정**: Wonyoung (test-engineer, 인프라 검증 겸임)

**변경 파일**:
- `docker-compose.yml`

**상세 작업**:

1. **Redis 리소스 제한 추가**:
   ```yaml
   redis:
     mem_limit: 1g
     memswap_limit: 1g
     cpus: 1.0
   ```

2. **TimescaleDB 리소스 제한 추가**:
   ```yaml
   timescaledb:
     mem_limit: 4g
     memswap_limit: 4g
     cpus: 2.0
   ```

3. **Promtail healthcheck 추가**:
   ```yaml
   promtail:
     healthcheck:
       test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:9080/ready"]
       interval: 15s
       timeout: 5s
       retries: 5
       start_period: 10s
   ```

4. **auto-tuner/monitoring healthcheck 검토**:
   - 현재 healthcheck: `python -c "import src.tuning.scheduled_tuner; print('ok')"` → import 성공만 확인
   - 개선: PID 파일 또는 마지막 실행 시각 체크로 변경
   - 대안: 현행 유지 (import 체크가 최소한의 생존 확인)

5. **전체 서비스 healthcheck 상태 확인 스크립트**:
   - `docker compose ps` 로 ALL healthy 확인
   - SSOT.md 업데이트: 14 → 15 서비스 (alertmanager 추가)

**Acceptance Criteria**:
- [ ] Redis에 `mem_limit: 1g` 설정
- [ ] TimescaleDB에 `mem_limit: 4g` 설정
- [ ] Promtail에 healthcheck 추가, healthy 상태
- [ ] `docker compose up` 후 ALL 서비스 healthy
- [ ] auto-tuner, monitoring 컨테이너 재시작 없이 healthy 유지

---

## 6. 개발자 배정 (IVE 팀)

| US | 담당 | 역할 | 예상 복잡도 |
|----|------|------|-----------|
| US-135 | Yujin | executor (lead) | HIGH — 스키마 통합 + migration runner 신규 개발 |
| US-136 | Gaeul | executor | LOW — .env 값 변경 + main.py/shadow.py 기본값 수정 |
| US-137 | Leeseo | executor | LOW — nginx.conf 포트 수정 + restart 정책 변경 |
| US-138 | Liz | executor | MEDIUM — alertmanager 신규 서비스 + datasource 정리 |
| US-139 | Wonyoung | test-engineer | LOW — docker-compose.yml 리소스 제한 + healthcheck |
| (검증) | Rei | designer/verifier | — Docker 전체 상태 + Grafana UI 확인 |

**병렬 실행 가능**: US-135 + US-136 동시 진행 (파일 충돌 없음)
**순차 필수**: US-137 → US-139 → US-138 (docker-compose.yml 충돌 방지)

---

## 7. 테스트 방법

### 7.1 Unit Tests
```bash
cd engine && python -m pytest tests/ -x --tb=short
# 기대: 4,346+ passed, 0 failed
```

### 7.2 Docker Integration
```bash
# 전체 스택 기동
docker compose up -d

# 전체 서비스 healthy 확인
docker compose ps
# 기대: 15/15 services healthy (alertmanager 추가)

# DB 스키마 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "\dt"
# 기대: ohlcv, spreads, signals, orderbook_snapshots, execution_log, ohlcv_1m,
#        adaptive_threshold_log, regime_detector_log, shadow_stage_results

# Materialized views 확인
docker compose exec timescaledb psql -U leviathan -d leviathan -c "\dm"
# 기대: strategy_daily_pnl, exchange_daily_pnl, pair_daily_pnl, ohlcv_1m (cagg), ohlcv_1h (cagg)

# Alertmanager 연결 확인
curl -s http://localhost:9093/-/healthy
# 기대: OK

# Prometheus targets 확인
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].health'
# 기대: all "up"
```

### 7.3 Shadow Test (Stage D)
```bash
cd engine && timeout 600 python -m src.main
# 기대: 10min shadow, PnL > 0, crash = 0
```

### 7.4 .env Preflight 검증
```bash
# MIN_EDGE_BPS 불일치 테스트 (의도적 불일치 후 WARNING 확인)
cd engine && python -c "
from src.modes.preflight import PreflightChecker
import asyncio
# ... preflight 실행 후 env_sync 체크 결과 확인
"
```

---

## 8. 리스크 및 주의사항

| 리스크 | 완화 방안 |
|--------|---------|
| docker-compose.yml 4개 US 동시 수정 충돌 | 순차 실행 (US-135→137→139→138) |
| init.sql이 기존 DB에서 실행 안 됨 (docker-entrypoint-initdb.d는 초기화시만) | migration_runner로 엔진 시작 시 보완 |
| Nginx WS 포트 변경 시 기존 연결 끊김 | FastAPI WS 서빙 포트 코드 확인 필수 (8000 vs 8001) |
| Alertmanager Telegram env var 미설정 시 알림 실패 | 기본값 빈 문자열 + 로그 WARNING |
| TimescaleDB mem_limit 4g가 부족할 수 있음 | 모니터링 후 조정 (현재 dev 환경 기준) |
| PowerLawSlippage k=0.0 변경 시 기존 테스트 실패 가능 | k=0이면 slippage=0 → 테스트에서 slippage 비교 로직 확인 |

---

## 9. Open Questions

- [ ] 엔진 WS가 FastAPI 내부(포트 8000)에서 서빙되는지, 별도 uvicorn(포트 8001)에서 서빙되는지 코드 확인 필요 — Nginx proxy_pass 포트 결정에 영향
- [ ] Alertmanager Telegram 환경변수를 `docker-compose.yml` environment로 전달할 때 `${}` 치환이 정상 동작하는지 확인 (alertmanager.yml 내 env var 참조 방식)
- [ ] db-backup/wal-backup을 `restart: unless-stopped`로 변경하면 스크립트 종료 후 즉시 재시작됨 — 이것이 의도된 동작인지, 아니면 sleep 루프가 필요한지
- [ ] `ohlcv` (timescale.py, time 컬럼)와 `ohlcv_1m` (migrations/001, ts 컬럼) 간의 관계 정리 — 동일 데이터의 중복 테이블인지, 각각 raw vs aggregated인지

---

## 10. 성공 기준 (Phase S3 완료 조건)

1. US-135~139 전체 prd.json `passes: true`
2. `docker compose up` 후 15/15 서비스 ALL healthy
3. DB에 전체 테이블 + materialized views 존재
4. pytest 4,346+ passed, 0 failed
5. Shadow 10min: PnL > 0, crash = 0
6. Prometheus → Alertmanager 연결 활성
7. Grafana datasource 3개 (Prometheus, TimescaleDB, Loki) 중복 없음
