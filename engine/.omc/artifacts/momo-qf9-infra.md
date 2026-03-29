# TF QF 9차 [단계 3] 인프라 무결성 검증 — Momo (인프라)
**날짜**: 2026-03-22
**검증자**: Momo (NewJeans 팀, qa-tester)
**대상**: docker-compose.yml, engine/.env, .env, nginx.conf, infra/backup/, engine/src/infra/

---

## 종합 판정: FAIL

**CRITICAL: 1건 / HIGH: 3건 / MEDIUM: 2건**

---

## 1. Docker 서비스 구성 + Healthcheck

### 실행 상태 (docker compose ps 실측값)
| 서비스 | 상태 | 포트 |
|--------|------|------|
| leviathan-timescaledb | Up 27 hours (healthy) | 5432 |
| leviathan-redis | Up 27 hours (healthy) | 6379 |
| leviathan-redis-exporter | Up 3 days (healthy) | 9121 |
| leviathan-prometheus | Up 3 days (healthy) | 9090 |
| leviathan-grafana | Up 3 days (healthy) | 3001 |
| leviathan-loki | Up 3 days (healthy) | 3100 |
| leviathan-promtail | **Up 3 days (unhealthy)** | - |
| leviathan-dashboard | **Up 2 days (unhealthy)** | 3000 |
| leviathan-alertmanager | Up 3 days (healthy) | 9093 |
| engine | **not running** | - |
| nginx | **not running** | - |
| bot-gateway | **not running** | - |

### 검증 결과

#### TC1: 핵심 인프라 (TimescaleDB + Redis) healthcheck
- **Expected**: healthy
- **Actual**: healthy (timescaledb: `pg_isready -U leviathan`, redis: `redis-cli -a ... ping`)
- **Status**: PASS

#### TC2: promtail healthcheck
- **Expected**: healthy (`http://localhost:9080/ready`)
- **Actual**: unhealthy — 3일째 비정상
- **Status**: FAIL [HIGH]

#### TC3: dashboard healthcheck
- **Expected**: healthy (`http://localhost:3000/api/health`)
- **Actual**: unhealthy — 2일째 비정상
- **Status**: FAIL [HIGH]

#### TC4: engine / nginx / bot-gateway 미실행
- **Expected**: TF QF 9차 인프라 검증은 DB/Redis 상시 실행 원칙에 따라 engine은 로컬 실행 대상
- **Actual**: docker에서 미실행 — 정책과 일치 (`docker compose up -d timescaledb redis` 원칙)
- **Status**: PASS (정책 준수)

#### TC5: Healthcheck 정의 완전성
- **Expected**: 모든 서비스에 healthcheck 정의
- **Actual**: db-backup (`restart: "no"`), wal-backup (`restart: "no"`) — 배치 작업으로 healthcheck 없음 (정상)
  - auto-tuner: import-check 방식 (`import src.tuning.scheduled_tuner`) — 기능 검증 미흡
- **Status**: PASS (배치 서비스 제외)

---

## 2. DB: TimescaleDB 스키마 + migration_runner

#### TC6: init.sql 통합 스키마 존재 확인
- **Evidence**: `/Users/100aniv/Development/arbitrage_OMC/docker/init.sql` 존재
- **Actual**: 8개 테이블 정의 확인 (orderbook_snapshots, execution_log, ohlcv_1m, ohlcv, + 추가 테이블), 모두 `IF NOT EXISTS` 멱등성 보장, hypertable 생성, retention policy 포함
- **Status**: PASS

#### TC7: migration_runner.py 어드바이저리 락 + 버전 트래킹
- **Evidence**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/db/migration_runner.py`
- **Actual**: `pg_advisory_lock(73318)` → `schema_version` 테이블 버전 체크 → `init.sql` 적용 → `pg_advisory_unlock(73318)` 순서 올바름. try/finally로 잠금 해제 보장. 멱등성 OK.
- **Status**: PASS

#### TC8: init.sql Docker 마운트 경로 일치
- **Expected**: docker-compose.yml `volumes: ./docker/init.sql:/docker-entrypoint-initdb.d/init.sql:ro` AND `./docker/init.sql:/app/docker/init.sql:ro`
- **Actual**: timescaledb 서비스 → `/docker-entrypoint-initdb.d/init.sql:ro` (정상). engine 서비스 → `/app/docker/init.sql:ro` (정상). migration_runner 탐색 경로 `/app/docker/init.sql` 일치.
- **Status**: PASS

---

## 3. Redis: EventBus 연결 + Stream 구조

#### TC9: EventBus 구현 완전성
- **Evidence**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/infra/redis/event_bus.py`
- **Actual**: XADD publish, XREADGROUP subscribe, XACK, dead-letter stream (`leviathan:dead_letter`), consumer group 멱등 생성 (BUSYGROUP 무시), PEL 관리 (XCLAIM 30s idle) 모두 구현됨
- **Status**: PASS

#### TC10: Redis 연결 설정 일치
- **Expected**: engine 서비스에서 `REDIS_HOST=redis`, `REDIS_URL=redis://redis:6379/0`
- **Actual**: docker-compose.yml engine 서비스 environment에 명시적 override 존재. .env의 `localhost` 값이 Docker 내부에서 `redis` hostname으로 재정의됨.
- **Status**: PASS

---

## 4. Nginx: TLS + 프록시

#### TC11: TLS 설정
- **Actual**: TLSv1.2/1.3만 허용, 강력한 cipher suite, ssl_session_tickets off, HSTS max-age=63072000 (2년) + preload
- OCSP stapling 비활성 (자체서명 인증서 사용 중 — 개발 환경 허용)
- **Status**: PASS

#### TC12: WebSocket 프록시 설정
- **Actual**: `/ws`, `/ws/feed` 각각 별도 location 블록, `proxy_http_version 1.1`, `Upgrade/Connection` 헤더 정상, `proxy_read_timeout 3600s` (장기 연결 허용)
- `$connection_upgrade` map 블록이 server 블록 밖에 정의됨 — 정상 (nginx context OK)
- **Status**: PASS

#### TC13: 대시보드 프록시 (CRITICAL)
- **Expected**: 대시보드 컨테이너 내부 URL `http://dashboard:3000`을 프록시해야 함
- **Actual**: nginx.conf `proxy_pass http://dashboard:3000` — Docker 내부 네트워크 올바름
- **BUT**: docker-compose.yml dashboard 서비스의 `NEXT_PUBLIC_ENGINE_URL=http://localhost:8000`, `NEXT_PUBLIC_WS_URL=ws://localhost:8000` — **Docker 네트워크에서 `localhost`는 dashboard 컨테이너 자신을 가리킴**. engine 서비스로 접근 불가.
- **Status**: FAIL [CRITICAL] — 대시보드가 engine API/WS에 접근 불가 (unhealthy 원인과 연관 가능)

#### TC14: /api/ prefix 처리
- **Actual**: nginx `location /api/` → `proxy_pass http://engine:8000` (prefix 유지). rewrite 라인은 주석처리됨. engine이 `/api/` prefix를 직접 처리하는 구조와 일치.
- **Status**: PASS

#### TC15: 보안 헤더
- **Actual**: HSTS, X-Frame-Options SAMEORIGIN, X-Content-Type-Options nosniff, CSP 정의됨. CSP에 `connect-src` → `ws: wss: http://localhost:8000 https://localhost:8000` 포함 (브라우저에서 직접 WS 연결 허용 — nginx를 통한 wss:// 방식과 혼재, 보안상 허용 수준)
- **Status**: PASS

---

## 5. .env 동기화: engine/.env vs root .env

#### TC16: 텔레그램 3-Bot 토큰 일치 (CRITICAL 발견)

**root .env (들여쓰기 주의 — 실제 파싱 문제 있음)**
```
  TRADE_TELEGRAM_BOT_TOKEN=8670372025:AAE7HtDil7ut8tgv3Qqb3HJJKUd8VzR53Mc
  INFRA_TELEGRAM_BOT_TOKEN=8650363801:AAH8BFtqymenwYvQV_HA0OGO99MZeSZjZm0
  DEV_TELEGRAM_BOT_TOKEN=8524564959:AAEfuunrqYftdJu7Ju7MCVgiYOrsmowFsWs
```
(들여쓰기 2칸 — 일부 .env 파서에서 키를 인식 못할 수 있음)

**engine/.env — 중복 블록 존재 (CRITICAL)**
- 라인 90-102: 1차 블록
  - `TRADE_TELEGRAM_BOT_TOKEN=8540777446:...` (구 토큰)
  - `INFRA_TELEGRAM_BOT_TOKEN=8540777446:...` (TRADE와 동일한 구 토큰 — 잘못된 값)
  - `DEV_TELEGRAM_BOT_TOKEN=8726269326:...`
- 라인 151-163: 2차 블록 (Phase S20에서 추가)
  - `TRADE_TELEGRAM_BOT_TOKEN=8670372025:...` (올바른 토큰)
  - `INFRA_TELEGRAM_BOT_TOKEN=8650363801:...` (올바른 토큰)
  - `DEV_TELEGRAM_BOT_TOKEN=8524564959:...` (올바른 토큰)

**문제**: `python-dotenv`는 파일에서 **첫 번째** 정의를 우선하므로, engine이 로드 시 라인 90-102의 구 토큰(잘못된 값)을 사용함. INFRA_TELEGRAM_BOT_TOKEN이 TRADE와 동일한 토큰 `8540777446`으로 설정되어 InfraBot이 TradeBot 토큰을 사용하게 됨.

- **Status**: FAIL [CRITICAL]

#### TC17: DB/Redis 설정 일치
| 항목 | root .env | engine/.env |
|------|-----------|-------------|
| DATABASE_URL | `postgresql+asyncpg://leviathan:leviathan@localhost:5432/leviathan` | 동일 |
| REDIS_URL | `redis://localhost:6379/0` | 동일 |
| REDIS_PASSWORD | `leviathan-redis-secret` | 동일 |
| DB_HOST | localhost | localhost |
| DB_PASSWORD | leviathan | leviathan |

Docker 실행 시 engine 서비스 environment에서 `DB_HOST=timescaledb`, `REDIS_HOST=redis`로 override됨 — 정상.
- **Status**: PASS

#### TC18: JWT_SECRET 프로덕션 값 미변경
- **Actual**: 양쪽 파일 모두 `JWT_SECRET=leviathan-dev-secret-change-in-production` — 기본값 그대로
- TF QF 9차 단계에서 프로덕션 준비 관점으로 허용 불가
- **Status**: FAIL [MEDIUM]

#### TC19: ENGINE_WS_PORT=8001 dead config
- **Actual**: root .env에 `ENGINE_WS_PORT=8001` 존재. docker-compose.yml 코멘트 `# 8001 removed: ENGINE_WS_PORT=8001 is dead config`로 명시. engine/.env에는 해당 키 없음. root .env에만 잔존.
- **Status**: FAIL [MEDIUM] — root .env 정리 필요

---

## 6. 백업: WAL 설정

#### TC20: WAL 아카이빙 설정
- **Actual**: docker-compose.yml timescaledb command에 `wal_level=replica`, `archive_mode=on`, `archive_command=test ! -f ... && cp ...`, `archive_timeout=300` (5분), `wal_keep_size=512MB` 모두 정의됨
- wal_archive 볼륨: `wal_archive:/var/lib/postgresql/wal_archive` 마운트
- **Status**: PASS

#### TC21: WAL 백업 스크립트 완전성
- **Actual**: `/Users/100aniv/Development/arbitrage_OMC/infra/backup/wal-backup.sh`
  - `backup` 명령: pg_dump + gzip + 7일 보존 + WAL 파일 2x 보존(14일) + RPO 60분 체크
  - `verify` 명령: 최신 백업 찾기 + gunzip 검증 + pg_restore --list 형식 검증 + WAL 아카이브 상태 체크
  - `restore-test` 명령: 임시 DB 생성 → 복원 → 테이블 수 확인 → 삭제
  - `set -euo pipefail` 안전 옵션 포함
- **Status**: PASS

#### TC22: db-backup 서비스 정의
- **Actual**: `restart: "no"` (배치 실행), pg_dump 기반, 7일 보존, db_backups 볼륨 사용
- 자동 스케줄링 없음 (cron 외부 설정 필요) — 문서화됨
- **Status**: PASS

---

## 7. Telegram 3-Bot 구조

#### TC23: 봇 분리 구조 (TelegramBotBase)
- **Actual**: `TelegramBotBase` — 공통 기반 (rate limit 20msg/min, chat_id 인증, poll_loop, callback 라우팅)
- `TradeTelegramBot` — `TRADE_TELEGRAM_BOT_TOKEN` 우선, `TELEGRAM_BOT_TOKEN` fallback, 20개 명령어
- `InfraTelegramBot` — `INFRA_TELEGRAM_BOT_TOKEN` 전용, 8개 명령어
- `DevTelegramBot` — `DEV_TELEGRAM_BOT_TOKEN` 우선, `WORKFLOW_TELEGRAM_BOT_TOKEN` fallback
- **Status**: PASS (구조 올바름)

#### TC24: InfraTelegramBot 토큰 충돌 (TC16 연계)
- **Actual**: engine/.env 1차 블록에서 `INFRA_TELEGRAM_BOT_TOKEN=8540777446`(TradeBot 토큰과 동일). InfraBot이 TradeBot의 토큰으로 초기화되어 두 봇이 동일 채널을 사용하게 됨. 봇 분리 목적 무효화.
- **Status**: FAIL (TC16 CRITICAL에 포함)

#### TC25: alertmanager sed 치환
- **Actual**: docker-compose.yml alertmanager entrypoint에서 `INFRA_TELEGRAM_BOT_TOKEN_PLACEHOLDER`, `TRADE_TELEGRAM_BOT_TOKEN_PLACEHOLDER`, `DEV_TELEGRAM_BOT_TOKEN_PLACEHOLDER` 3개 모두 치환. `env_file: .env` 포함. 단, root .env의 들여쓰기 문제(TC16)로 인해 토큰 변수가 빈값으로 읽힐 수 있음.
- **Status**: WARN (root .env 들여쓰기 수정 후 재검증 필요)

---

## 발견사항 요약

### CRITICAL (1건)
| ID | 항목 | 파일 | 내용 |
|----|------|------|------|
| C1 | engine/.env 중복 토큰 블록 | `engine/.env` L90-102 vs L151-163 | TRADE/INFRA/DEV 토큰이 파일 내 2회 정의. python-dotenv는 첫 번째 값 우선이므로 구 토큰(L90 블록) 적용. INFRA_TELEGRAM_BOT_TOKEN=TRADE 토큰(동일값) — 봇 분리 무효화 |

### HIGH (3건)
| ID | 항목 | 파일 | 내용 |
|----|------|------|------|
| H1 | dashboard 컨테이너 unhealthy | docker-compose.yml, live | `leviathan-dashboard` 2일째 unhealthy. `NEXT_PUBLIC_ENGINE_URL=http://localhost:8000`이 컨테이너 내부에서 자신을 가리켜 engine API 접근 불가. `http://engine:8000`으로 수정 필요 |
| H2 | promtail unhealthy | docker-compose.yml, live | `leviathan-promtail` 3일째 unhealthy. 로그 수집 중단. `/var/run/docker.sock` 마운트 또는 promtail-config.yaml 설정 문제 가능 |
| H3 | root .env 들여쓰기 오류 | `.env` L102, L104, L106 | TRADE/INFRA/DEV 토큰 라인에 2칸 들여쓰기. docker-compose `env_file: .env` 파싱 시 키 인식 실패 가능. alertmanager sed 치환에 빈값 주입 위험 |

### MEDIUM (2건)
| ID | 항목 | 파일 | 내용 |
|----|------|------|------|
| M1 | JWT_SECRET 기본값 미변경 | `.env`, `engine/.env` | `leviathan-dev-secret-change-in-production` 그대로. TF QF → SF → Live 전환 전 반드시 변경 필요 |
| M2 | ENGINE_WS_PORT=8001 dead config | `.env` L19 | 실제로 사용하지 않는 dead 설정 잔존. root .env에서 제거 필요 |

---

## 검증 환경
- tmux session: `qa-infra-docker-*` (검증 후 kill 완료)
- docker compose ps: 실측값 기반
- 파일 정적 분석: docker-compose.yml, nginx.conf, .env, engine/.env, migration_runner.py, event_bus.py, wal-backup.sh, telegram_bot_base.py, telegram_trade_bot.py, telegram_infra_bot.py, telegram_dev_bot.py

## Cleanup
- tmux sessions killed: YES
- Artifacts written: YES

---

## 수정 지시사항 (우선순위 순)

1. **[C1] engine/.env L90-102 삭제**: 1차 블록(구 토큰) 제거. L151-163 블록(Phase S20 블록)만 유지
2. **[H1] docker-compose.yml dashboard NEXT_PUBLIC_ENGINE_URL 수정**: `http://localhost:8000` → `http://engine:8000`, `ws://localhost:8000` → `ws://engine:8000`
3. **[H3] root .env 들여쓰기 수정**: L102, L104, L106 앞 2칸 공백 제거
4. **[H2] promtail 문제 조사**: `docker logs leviathan-promtail` 확인, promtail-config.yaml 검토
5. **[M1] JWT_SECRET 변경**: 프로덕션 전환 전 강한 랜덤 시크릿으로 교체
6. **[M2] root .env ENGINE_WS_PORT=8001 제거**
