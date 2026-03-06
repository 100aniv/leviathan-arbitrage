# Runbook 06 — Security & API Key Management

**Severity:** CRITICAL (key compromise) / HIGH (routine rotation)
**SLA:** Key compromise: revoke within 5 minutes. Routine rotation: complete within 30-minute maintenance window.
**Related code:** `engine/src/core/config.py`, `engine/src/infra/exchange/__init__.py`, `engine/src/infra/telegram.py`

---

## Overview

LEVIATHAN의 모든 거래소 API 키는 단 하나의 원칙을 따른다: **출금 권한은 절대 부여하지 않는다.**
키가 유출되더라도 자금을 인출할 수 없어야 한다. 이 Runbook은 키 저장 전략, 거래소별 설정,
정기 교체 절차, 보안 체크리스트, 침해 대응, Docker 시크릿 관리를 다룬다.

---

## 1. API Key 저장 전략

### 1.1 환경별 저장 방식

| 환경 | 저장 방식 | 비고 |
|------|-----------|------|
| Development | `.env` 파일 (로컬 전용) | git에 절대 커밋 금지 |
| Staging/Sandbox | `.env` 파일 또는 CI/CD 시크릿 | testnet 키만 사용 |
| Production | Docker Secrets 또는 외부 Secrets Manager | 환경변수 블록 사용 금지 |

### 1.2 키 네이밍 규칙

`engine/src/core/config.py`의 `ExchangeSettings`에 정의된 규칙을 따른다:

```
{EXCHANGE}_API_KEY       # API 키 (공개 식별자)
{EXCHANGE}_API_SECRET    # API 시크릿 (서명용, 절대 노출 금지)
{EXCHANGE}_PASSWORD      # Passphrase (OKX, Bitget만 해당)
{EXCHANGE}_TESTNET       # true/false (testnet 모드 스위치)
```

구체적 예시:

```bash
# Binance
BINANCE_API_KEY=abc123...
BINANCE_API_SECRET=xyz789...
BINANCE_TESTNET=false

# OKX (passphrase 필수)
OKX_API_KEY=abc123...
OKX_API_SECRET=xyz789...
OKX_PASSPHRASE=my_passphrase
OKX_TESTNET=false

# Bybit
BYBIT_API_KEY=abc123...
BYBIT_API_SECRET=xyz789...
BYBIT_TESTNET=false

# Bitget (passphrase 필수)
BITGET_API_KEY=abc123...
BITGET_API_SECRET=xyz789...
BITGET_PASSWORD=my_passphrase

# Upbit (access/secret 구조)
UPBIT_API_KEY=abc123...
UPBIT_API_SECRET=xyz789...

# Bithumb
BITHUMB_API_KEY=abc123...
BITHUMB_API_SECRET=xyz789...

# Telegram
TELEGRAM_BOT_TOKEN=123456789:AAF...
TELEGRAM_CHAT_ID=-100123456789
```

### 1.3 .env 파일 설정

```bash
# 프로젝트 루트에서 .gitignore 확인
grep -n "\.env" .gitignore
# 출력에 반드시 '.env' 또는 '*.env' 가 포함되어야 함
```

`.gitignore`에 없다면 즉시 추가:

```bash
echo ".env" >> .gitignore
echo ".env.*" >> .gitignore
echo "!.env.example" >> .gitignore  # 예시 파일은 커밋 허용
git add .gitignore && git commit -m "security: ensure .env is gitignored"
```

git 히스토리에 키가 이미 포함된 경우 Section 5 (침해 대응)를 즉시 따른다.

### 1.4 .env.example 유지

실제 값 없이 구조만 문서화한 `.env.example`을 항상 커밋 상태로 유지한다:

```bash
# engine/.env.example
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_TESTNET=false

OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=
OKX_TESTNET=false

BYBIT_API_KEY=
BYBIT_API_SECRET=
BYBIT_TESTNET=false

BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_PASSWORD=

UPBIT_API_KEY=
UPBIT_API_SECRET=

BITHUMB_API_KEY=
BITHUMB_API_SECRET=

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

DATABASE_URL=postgresql+asyncpg://leviathan:leviathan@localhost:5432/leviathan
REDIS_URL=redis://localhost:6379/0
```

---

## 2. 거래소별 API Key 설정

### 공통 원칙 (모든 거래소 적용)

```
권한 설정:
  [x] 조회 (Read / View)
  [x] 거래 (Trade / Spot / Futures)
  [ ] 출금 (Withdraw) — 절대 활성화 금지
  [ ] 자금 이체 (Transfer) — 활성화 금지

IP 화이트리스트:
  - 엔진 서버 IP만 등록 (최대 3개)
  - 0.0.0.0/0 (전체 허용)은 절대 사용 금지
```

### 2.1 Binance

**필요 권한:** `Enable Reading`, `Enable Spot & Margin Trading`

```
거래소 설정 페이지: https://www.binance.com/en/my/settings/api-management
서브계정: 권장 — 별도 서브계정에서 Trading API 생성 (메인 계정 분리)
IP 화이트리스트: 필수 (Binance는 IP 미설정 키의 출금 권한을 자동 허용하므로 반드시 설정)
Rate limit: 1200 req/min (BINANCE_RATE_LIMIT 기본값)
```

Testnet 설정:

```bash
# Binance Testnet: https://testnet.binance.vision/
BINANCE_API_KEY=<testnet_key>
BINANCE_API_SECRET=<testnet_secret>
BINANCE_TESTNET=true
```

Testnet 연결 검증:

```python
from engine.src.infra.exchange import create_native_adapter

adapter = create_native_adapter(
    exchange_id="binance",
    api_key="<testnet_key>",
    api_secret="<testnet_secret>",
    sandbox=True,
)
result = await adapter.get_health_score()
print(f"Binance testnet health: {result}")  # >= 0.95 이어야 함
```

### 2.2 Bybit

**필요 권한:** `Read-Write` → `Trade` 체크, `Withdraw` 비활성화

```
거래소 설정 페이지: https://www.bybit.com/app/user/api-management
서브계정: 권장 — Unified Trading Account 서브계정 사용
IP 화이트리스트: 필수
Rate limit: 600 req/min (BYBIT_RATE_LIMIT 기본값)
```

Testnet 설정:

```bash
# Bybit Testnet: https://testnet.bybit.com/
BYBIT_API_KEY=<testnet_key>
BYBIT_API_SECRET=<testnet_secret>
BYBIT_TESTNET=true
```

### 2.3 OKX

**필요 권한:** `Read`, `Trade`; Passphrase는 반드시 설정 필요

```
거래소 설정 페이지: https://www.okx.com/account/my-api
서브계정: 강력 권장 — 메인 계정 키는 자금 이체 권한 포함 가능하므로 반드시 서브계정 사용
IP 화이트리스트: 필수 (OKX는 화이트리스트 없는 키의 API 호출을 24시간 후 자동 만료)
Rate limit: 600 req/min (OKX_RATE_LIMIT 기본값)
Passphrase: 영숫자+특수문자 조합 16자 이상 권장
```

Testnet 설정:

```bash
# OKX Demo Trading: https://www.okx.com/demo-trading
OKX_API_KEY=<demo_key>
OKX_API_SECRET=<demo_secret>
OKX_PASSPHRASE=<demo_passphrase>
OKX_TESTNET=true
```

### 2.4 Bitget

**필요 권한:** `Read`, `Trade`; Passphrase 필수

```
거래소 설정 페이지: https://www.bitget.com/account/newapi
서브계정: 권장 — 메인 계정과 트레이딩 계정 분리
IP 화이트리스트: 필수
Passphrase: BITGET_PASSWORD 환경변수로 주입
```

Testnet 설정:

```bash
# Bitget Simulated Trading 사용
BITGET_API_KEY=<simulated_key>
BITGET_API_SECRET=<simulated_secret>
BITGET_PASSWORD=<simulated_passphrase>
# Bitget은 sandbox=True로 시뮬레이션 엔드포인트 자동 전환
```

### 2.5 Upbit (한국)

**필요 권한:** `자산 조회`, `주문 조회`, `주문 하기`; `출금하기` 비활성화

```
거래소 설정 페이지: https://upbit.com/service_center/open_api_info
IP 화이트리스트: 필수 (한국 KYC 정책상 화이트리스트 없는 키는 조회만 허용)
주의: Upbit은 서브계정 미지원 — 메인 계정 키 사용
Rate limit: 기본 10 req/sec (초과 시 429 에러)
```

### 2.6 Bithumb (한국)

**필요 권한:** 거래 API (`trade`); 출금 API (`withdraw`) 비활성화

```
거래소 설정 페이지: https://www.bithumb.com/u1/US127
IP 화이트리스트: 필수
주의: Bithumb은 서브계정 미지원
Rate limit: 기본 20 req/sec
```

### 2.7 Monitoring-only (Read-only) 키

트레이딩 키와 별도로 모니터링 전용 read-only 키를 발급하여 Grafana/Prometheus 연동에 사용한다:

```bash
# Read-only keys for monitoring (trade permission OFF)
BINANCE_READONLY_API_KEY=...
BINANCE_READONLY_API_SECRET=...
```

---

## 3. Key Rotation 절차

### 3.1 정기 교체 주기

```
트레이딩 키:   90일마다 교체 (필수)
Testnet 키:    6개월마다 교체 (또는 침해 감지 시 즉시)
Read-only 키:  6개월마다 교체
Telegram 봇:   연 1회 또는 침해 감지 시 즉시
```

다음 교체 일정 추적:

```sql
-- TimescaleDB에 key rotation 일정 기록 (권장)
INSERT INTO system_events (event_type, metadata, ts)
VALUES ('key_rotation_scheduled', '{"exchange":"binance","due":"2026-06-07"}', NOW());
```

### 3.2 무중단 교체 절차 (Zero-Downtime Rotation)

**전제조건:** 신규 키 발급 → 기존 키 유지 → 엔진 전환 → 구 키 폐기 순서로 진행.
절대로 구 키를 먼저 폐기하지 않는다.

**Step 1 — 거래소에서 신규 키 발급**

```
1. 거래소 API 관리 페이지에서 새 키 생성
2. 기존 키와 동일한 권한 설정 (Trade only, NO Withdraw)
3. 동일한 IP 화이트리스트 적용
4. 신규 키/시크릿을 임시로 안전한 곳에 보관 (패스워드 매니저 권장)
```

**Step 2 — 엔진을 Shadow Mode로 전환**

```bash
# 실행 중인 엔진을 shadow mode로 전환 (실거래 중단, 모니터링 유지)
ENGINE_PID=$(pgrep -f "python.*leviathan.*main")
kill -SIGUSR1 $ENGINE_PID

# 전환 확인
journalctl -u leviathan --since "30s ago" | grep "shadow_mode_active"
```

**Step 3 — .env 업데이트**

```bash
# 현재 .env 백업 (키 값 포함이므로 암호화 저장소에만 보관)
cp /etc/leviathan/engine.env /etc/leviathan/engine.env.bak_$(date +%Y%m%d)
chmod 600 /etc/leviathan/engine.env.bak_$(date +%Y%m%d)

# 신규 키로 업데이트 (예: Binance)
# sed 또는 직접 편집기로 BINANCE_API_KEY, BINANCE_API_SECRET 값 교체
```

**Step 4 — 엔진 재시작 및 신규 키 검증**

```bash
sudo systemctl restart leviathan-engine

# 시작 확인
timeout 60 bash -c 'until journalctl -u leviathan --since "1 min ago" | grep -q "engine_ready"; do sleep 2; done'
echo "Engine ready with new keys"
```

```python
# 신규 키로 인증 성공 여부 검증
import asyncio
from engine.src.infra.exchange import create_native_adapter

async def verify_new_key(exchange_id: str, api_key: str, api_secret: str, passphrase: str = ""):
    adapter = create_native_adapter(
        exchange_id=exchange_id,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
    )
    score = await adapter.get_health_score()
    balance = await adapter.fetch_balance()
    print(f"{exchange_id}: health={score:.3f}, balance_keys={list(balance.keys())[:3]}")
    assert score >= 0.95, f"Health check failed: {score}"
    print(f"{exchange_id}: NEW KEY VERIFIED OK")

asyncio.run(verify_new_key("binance", "NEW_KEY", "NEW_SECRET"))
```

**Step 5 — 구 키 폐기**

```
1. 거래소 API 관리 페이지에서 구 키(OLD KEY) 삭제
2. 구 키가 더 이상 동작하지 않는지 확인 (삭제 후 401 응답 예상)
3. .env 백업 파일에서 구 키 값 삭제 후 재암호화
```

교체 완료 기록:

```python
import asyncio
from engine.src.infra.telegram import TelegramNotifier

async def notify_rotation():
    n = TelegramNotifier.from_env()
    await n.send("KEY ROTATION: binance key rotated successfully. Old key revoked.")

asyncio.run(notify_rotation())
```

### 3.3 긴급 교체 (Key Compromise 감지 시)

Section 5 (침해 대응)를 즉시 따른다. 긴급 교체는 무중단이 아닌 즉시 폐기를 우선으로 한다.

---

## 4. Live Trading 보안 체크리스트

엔진을 live 모드로 전환하기 전 아래 모든 항목을 확인한다.

### 4.1 API Key 권한 검증

```
[ ] 모든 거래소 키에서 출금(Withdraw) 권한 비활성화 확인
[ ] 모든 거래소 키에서 자금 이체(Transfer) 권한 비활성화 확인
[ ] Trade 권한만 활성화되어 있음
[ ] Read-only 키와 Trading 키가 분리되어 있음
```

거래소 API로 권한 확인:

```python
import asyncio
from engine.src.infra.exchange import create_native_adapter

async def audit_permissions():
    exchanges = [
        ("binance", "BINANCE_API_KEY", "BINANCE_API_SECRET", ""),
        ("bybit",   "BYBIT_API_KEY",   "BYBIT_API_SECRET",   ""),
        ("okx",     "OKX_API_KEY",     "OKX_API_SECRET",     "OKX_PASSPHRASE"),
        ("bitget",  "BITGET_API_KEY",  "BITGET_API_SECRET",  "BITGET_PASSWORD"),
    ]
    import os
    for exc_id, key_env, secret_env, pass_env in exchanges:
        adapter = create_native_adapter(
            exchange_id=exc_id,
            api_key=os.getenv(key_env, ""),
            api_secret=os.getenv(secret_env, ""),
            passphrase=os.getenv(pass_env, ""),
        )
        perms = await adapter.fetch_api_permissions()  # exchange-specific
        print(f"{exc_id}: {perms}")
        assert "withdraw" not in str(perms).lower() or perms.get("withdraw") is False, \
            f"CRITICAL: {exc_id} has withdraw permission!"

asyncio.run(audit_permissions())
```

### 4.2 IP 화이트리스트 검증

```
[ ] Binance: API 설정에서 "Restrict access to trusted IPs only" 활성화
[ ] Bybit: IP restriction 설정에 엔진 서버 IP 등록
[ ] OKX: IP allowlist에 엔진 서버 IP 등록
[ ] Bitget: IP whitelist 설정 완료
[ ] Upbit: IP 허용 목록 설정 완료
[ ] Bithumb: IP 허용 등록 완료
[ ] 0.0.0.0/0 또는 "All IPs" 설정이 없음을 확인
```

### 4.3 계정 보안 설정

```
[ ] 모든 거래소 계정에서 2FA (TOTP/Google Authenticator) 활성화
[ ] 거래소 계정 이메일에 2FA 활성화
[ ] 로그인 알림 이메일/SMS 활성화
[ ] 거래소 API 호출 알림 활성화 (지원하는 거래소만)
[ ] 서브계정과 메인 계정의 자금 이체 권한 상호 검토
```

### 4.4 환경 보안

```
[ ] .env 파일이 git에 포함되지 않음 (git ls-files | grep -v "^!" | grep ".env" 결과 없음)
[ ] .env 파일 권한: chmod 600 (소유자만 읽기/쓰기)
[ ] Telegram bot token이 특정 chat_id만 허용 (TELEGRAM_CHAT_ID 설정)
[ ] DATABASE_URL 패스워드가 기본값이 아님
[ ] Redis AUTH 패스워드 설정 (REDIS_URL에 :password@ 포함)
```

```bash
# .env 권한 확인
ls -la /etc/leviathan/engine.env
# -rw------- 1 leviathan leviathan ... engine.env

# git에 키가 없음을 확인
git ls-files | grep "\.env$"
# 출력 없어야 함

git log --all --full-history -- "*.env" | head -5
# .env 관련 커밋 없어야 함
```

### 4.5 거래소 레벨 주문 제한

```
[ ] 거래소 API에서 최대 주문 금액 제한 설정 (지원하는 경우)
[ ] 일일 거래량 한도 설정 (지원하는 경우)
[ ] 엔진 측 RISK_MAX_SINGLE_TRADE_PCT 기본값(5%) 확인
[ ] 엔진 측 RISK_MAX_EXPOSURE_PCT 기본값(30%) 확인
```

```python
# 현재 리스크 파라미터 확인
from engine.src.core.config import RiskSettings

risk = RiskSettings()
print(f"Max single trade: {risk.max_single_trade_pct * 100:.0f}%")
print(f"Max exposure:     {risk.max_exposure_pct * 100:.0f}%")
print(f"Max drawdown:     {risk.max_drawdown_pct * 100:.0f}%")
print(f"Kill switch:      {'enabled' if risk.kill_switch_enabled else 'DISABLED'}")
assert risk.kill_switch_enabled, "Kill switch must be enabled for live trading"
```

### 4.6 Kill Switch 및 모니터링

```
[ ] Kill switch 동작 테스트 완료 (Runbook 01 참조)
[ ] Telegram 알림 정상 수신 확인
[ ] Read-only 키로 포지션 모니터링 별도 구성
[ ] Prometheus/Grafana 알림 규칙 설정
[ ] 비정상 거래 감지 알림 설정 (24시간 이내 비정상 손실 임계값)
```

---

## 5. 침해 대응: Key Compromise

### 5.1 침해 감지 징후

```
- 예상치 못한 주문/거래 발생
- 알 수 없는 IP에서의 API 호출 (거래소 API 로그 확인)
- API 인증 에러 급증 (circuit_breaker_api_error_rate 초과)
- 거래소 이메일 알림: "새 장치에서 로그인"
- 계정 잔고 예상치 못한 변동
- git 히스토리에서 .env 파일 발견
```

### 5.2 즉각 조치 (5분 이내)

**Step 1 — 엔진 즉시 중단**

```bash
# 프로세스 즉시 종료 (graceful shutdown 불필요, 속도 우선)
sudo systemctl stop leviathan-engine

# 또는 강제 종료
kill -9 $(pgrep -f "python.*leviathan.*main")

# 확인
pgrep -f "python.*leviathan" || echo "Engine stopped"
```

**Step 2 — 모든 거래소에서 API 키 즉시 폐기**

```
순서: 가장 큰 잔고를 보유한 거래소부터 우선 처리

Binance:  https://www.binance.com/en/my/settings/api-management → 키 삭제
Bybit:    https://www.bybit.com/app/user/api-management → 키 삭제
OKX:      https://www.okx.com/account/my-api → 키 삭제
Bitget:   https://www.bitget.com/account/newapi → 키 삭제
Upbit:    https://upbit.com/service_center/open_api_info → 키 삭제
Bithumb:  https://www.bithumb.com/u1/US127 → 키 삭제
```

**Step 3 — 미체결 주문 전량 취소**

```python
# 각 거래소에서 모든 미체결 주문 취소 (read-only 키가 있다면 그것으로도 확인)
import asyncio
import os
from engine.src.infra.exchange import create_native_adapter

async def cancel_all_orders_emergency():
    exchanges = [
        ("binance", os.getenv("BINANCE_API_KEY",""), os.getenv("BINANCE_API_SECRET",""), ""),
        ("bybit",   os.getenv("BYBIT_API_KEY",""),   os.getenv("BYBIT_API_SECRET",""),   ""),
        ("okx",     os.getenv("OKX_API_KEY",""),     os.getenv("OKX_API_SECRET",""),     os.getenv("OKX_PASSPHRASE","")),
    ]
    for exc_id, key, secret, passphrase in exchanges:
        if not key:
            print(f"{exc_id}: no key configured, skipping")
            continue
        try:
            adapter = create_native_adapter(exc_id, key, secret, passphrase)
            cancelled = await adapter.cancel_all_orders()
            print(f"{exc_id}: cancelled {len(cancelled)} orders")
        except Exception as e:
            print(f"{exc_id}: FAILED to cancel orders: {e}")
            print(f"  -> Manually cancel via exchange UI immediately!")

asyncio.run(cancel_all_orders_emergency())
```

**Step 4 — Telegram 긴급 알림 발송 (봇 토큰이 안전한 경우)**

```python
import asyncio
from engine.src.infra.telegram import TelegramNotifier

async def alert():
    n = TelegramNotifier.from_env()
    await n.send(
        "SECURITY INCIDENT: API key compromise suspected. "
        "Engine HALTED. All keys revoked. Manual investigation required."
    )

asyncio.run(alert())
```

### 5.3 조사 (1시간 이내)

**거래소 API 로그 확인:**

```
각 거래소 API 접근 로그에서:
[ ] 침해 시점 추정 (최초 비정상 API 호출 시각)
[ ] 호출 IP 주소 목록 확인 (허가된 IP 외 접근 여부)
[ ] 발생한 주문/거래 목록 수집
[ ] 출금 시도 여부 확인 (출금 권한 없음 → 차단되어야 함)
```

**내부 로그 분석:**

```bash
# 엔진 로그에서 API 에러 및 비정상 활동 추출
journalctl -u leviathan --since "24 hours ago" | \
    python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line)
        if any(k in e.get('event','') for k in ['auth', 'api_error', 'order', 'key']):
            print(json.dumps(e, indent=2))
    except: pass
" | head -200
```

**git 히스토리 검사:**

```bash
# .env 또는 시크릿이 커밋된 적 있는지 확인
git log --all --full-history -- "*.env" "*.secret" ".env*"

# 커밋 내용에서 키 패턴 검색 (HMAC 시크릿은 보통 64자 hex)
git log --all -p | grep -E "[A-Fa-f0-9]{64}" | head -20
```

### 5.4 복구 (조사 완료 후)

```
1. 침해 범위 확정 (어느 키가, 언제부터, 어떤 작업이 수행됐는지)
2. 영향받은 모든 거래소 계정의 2FA 재설정
3. 거래소 계정 이메일 비밀번호 변경
4. 새 API 키 발급 (Section 2의 절차 따름, 새 IP 화이트리스트 적용)
5. 새 키로 .env 업데이트 (이전 .env 파일 완전 삭제 후 재생성)
6. 엔진 재시작 전 전체 보안 체크리스트 재수행 (Section 4)
7. Shadow mode로 24시간 검증 후 live 전환
```

### 5.5 사후 검토 (24시간 이내)

```
검토 항목:
[ ] 침해 원인 (git 노출, 서버 침해, 피싱, 내부자)
[ ] 타임라인 작성 (침해 → 감지 → 대응 → 복구)
[ ] 재발 방지책 수립
[ ] 재무 손실 평가 (거래 손실 + 수수료)
[ ] 필요 시 거래소에 비정상 거래 이의 제기

보고서 항목:
- 침해 경로
- 대응 시간 (목표: 5분 이내 엔진 중단, 15분 이내 전 키 폐기)
- 개선 사항 (Secrets Manager 도입, 키 권한 추가 제한 등)
```

---

## 6. Docker 보안

### 6.1 Secrets 주입 방식

**권장: Docker Secrets (production)**

```yaml
# docker-compose.yml
version: "3.8"
services:
  leviathan-engine:
    image: leviathan-engine:latest
    secrets:
      - binance_api_key
      - binance_api_secret
      - okx_api_key
      - okx_api_secret
      - okx_passphrase
      - bybit_api_key
      - bybit_api_secret
      - telegram_bot_token
    environment:
      # 비민감 설정만 environment 블록에 작성
      - EXECUTION_MODE=live
      - TRADING_ACTIVE_EXCHANGES=binance,bybit,okx
      - RISK_MAX_EXPOSURE_PCT=0.30
    # 민감 값은 environment 블록에 절대 작성 금지

secrets:
  binance_api_key:
    external: true   # docker secret create binance_api_key <(echo "KEY_VALUE")
  binance_api_secret:
    external: true
  # ... 나머지 시크릿
```

Docker Secrets 생성:

```bash
# 각 시크릿을 Docker Swarm secret으로 등록
printf "YOUR_BINANCE_API_KEY"    | docker secret create binance_api_key -
printf "YOUR_BINANCE_API_SECRET" | docker secret create binance_api_secret -
printf "YOUR_OKX_API_KEY"        | docker secret create okx_api_key -
printf "YOUR_OKX_API_SECRET"     | docker secret create okx_api_secret -
printf "YOUR_OKX_PASSPHRASE"     | docker secret create okx_passphrase -
printf "YOUR_BYBIT_API_KEY"      | docker secret create bybit_api_key -
printf "YOUR_BYBIT_API_SECRET"   | docker secret create bybit_api_secret -
printf "YOUR_TELEGRAM_BOT_TOKEN" | docker secret create telegram_bot_token -
```

**차선책: env_file (staging/single-node)**

```yaml
# docker-compose.yml (staging)
services:
  leviathan-engine:
    image: leviathan-engine:latest
    env_file:
      - /etc/leviathan/engine.env   # 호스트의 protected .env 파일
    # environment: 블록에 민감 값 직접 작성 금지
    # BAD:  environment: [BINANCE_API_KEY=abc123]
    # GOOD: env_file: [/etc/leviathan/engine.env]
```

### 6.2 컨테이너 네트워크 격리

```yaml
# docker-compose.yml
services:
  leviathan-engine:
    networks:
      - engine_internal  # DB, Redis 접근용
      - engine_external  # 거래소 API 접근용 (egress only)

  leviathan-db:
    networks:
      - engine_internal  # engine에서만 접근 가능, 외부 노출 없음

networks:
  engine_internal:
    internal: true   # 외부 인터넷 접근 차단
  engine_external:
    # 거래소 API 도메인에 대한 egress만 허용 (방화벽 규칙과 함께 사용)
```

### 6.3 Non-root 사용자 실행

```dockerfile
# engine/Dockerfile
FROM python:3.11-slim

# 전용 사용자 생성 (UID/GID 고정)
RUN groupadd --gid 10001 leviathan && \
    useradd --uid 10001 --gid leviathan --no-create-home --shell /bin/false leviathan

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -e ".[prod]" && \
    chown -R leviathan:leviathan /app

# non-root로 전환
USER leviathan

ENTRYPOINT ["python", "-m", "engine.src.main"]
```

```bash
# 컨테이너 내 실행 사용자 확인
docker exec leviathan-engine whoami
# leviathan (root가 아니어야 함)
```

### 6.4 Read-only Filesystem

```yaml
# docker-compose.yml
services:
  leviathan-engine:
    image: leviathan-engine:latest
    read_only: true           # 루트 파일시스템 읽기 전용
    tmpfs:
      - /tmp:size=128m        # 임시 파일은 tmpfs에만 허용
      - /run:size=64m
    volumes:
      - /var/log/leviathan:/var/log/leviathan:rw  # 로그는 마운트로 허용
```

### 6.5 이미지 보안

```bash
# 이미지에 시크릿이 포함되지 않았는지 확인
docker history leviathan-engine:latest --no-trunc | grep -iE "key|secret|password|token"
# 출력 없어야 함

# 이미지 레이어에서 .env 파일 확인
docker run --rm leviathan-engine:latest find / -name "*.env" 2>/dev/null
# 출력 없어야 함

# 이미지 취약점 스캔 (trivy 사용 권장)
trivy image leviathan-engine:latest --severity HIGH,CRITICAL
```

---

## 7. 에스컬레이션 연락처

| 상황 | 조치 | 채널 |
|------|------|------|
| 키 침해 감지 | 엔진 중단 → Section 5 즉시 실행 | Telegram @leviathan_ops |
| 거래소 계정 잠금 | 거래소 고객지원 + 키 폐기 | 거래소 공식 서포트 채널 |
| 90일 교체 기한 초과 | 즉시 교체 일정 수립 | 운영 채널 |
| Docker secret 손상 | 컨테이너 중단 → secret 재생성 | 시스템 관리자 |

**에스컬레이션 임계값:** 키 침해 의심 시 즉시 (5분 이내 엔진 중단). 확인 전 재시작 금지.

---

## References

- Exchange settings: `engine/src/core/config.py:ExchangeSettings`
- Native adapter factory: `engine/src/infra/exchange/__init__.py:create_native_adapter`
- Telegram notifier: `engine/src/infra/telegram.py`
- Kill switch halt: `engine/src/risk/kill_switch.py`
- Live gate pre-flight: `engine/src/modes/live_gate.py`
- Deployment runbook: `engine/docs/runbooks/05_deployment.md`
- Kill switch runbook: `engine/docs/runbooks/01_kill_switch_recovery.md`
- QUANT_MANIFESTO.md Section 8 (Live Readiness), Section 9 (Security)
