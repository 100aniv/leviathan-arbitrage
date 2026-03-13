# Phase S1: Security Hardening — Implementation Plan

**Date**: 2026-03-13
**Phase**: S1 (Security Hardening)
**Stories**: US-152, US-123, US-124, US-125, US-126, US-127, US-128 (7 US)
**Source**: TF Semi-Final findings (CRITICAL 3 + HIGH 4 security issues)

---

## 1. Execution Order (Priority)

| Order | US | Title | Priority | Complexity |
|-------|-----|-------|----------|------------|
| 1 | US-152 | API 키 로테이션 + .gitignore + pre-commit | 0 | LOW (config only) |
| 2 | US-123 | 전 엔드포인트 JWT 인증 강제 | 1 | MEDIUM (11 endpoints) |
| 3 | US-124 | JWT 시크릿 강화 + prod fail-fast | 2 | LOW (1 file delta) |
| 4 | US-125 | Nginx IP whitelist + XFF 프록시 신뢰 | 3 | MEDIUM (2 files) |
| 5 | US-126 | Redis 인증 + dangerous commands 비활성 | 4 | MEDIUM (3 files) |
| 6 | US-127 | CSP 헤더 강화 (Nginx + Next.js) | 5 | LOW (2 files) |
| 7 | US-128 | pytest backoff 테스트 수정 | 6 | LOW (1 file) |

**Batching**: All 7 US touch independent files → parallel development possible.
- Batch A (engine/api): US-123, US-124 (Yujin)
- Batch B (infra): US-125, US-126, US-127 (Gaeul)
- Batch C (config): US-152 (Lead direct — operational, not code)
- Batch D (test): US-128 (Wonyoung)

---

## 2. Detailed Implementation

### US-152: API 키 로테이션 + .gitignore + pre-commit (Priority 0)

**Code-testable tasks:**
1. `.gitignore` — add explicit entries:
   ```
   .env
   engine/.env
   *.env.local
   ```
2. Create pre-commit hook `.githooks/pre-commit`:
   ```bash
   #!/bin/bash
   # Block .env file commits
   if git diff --cached --name-only | grep -qE '\.env$|\.env\.local$'; then
     echo "ERROR: .env files must not be committed"
     exit 1
   fi
   ```
3. `git config core.hooksPath .githooks`

**Operational tasks (manual, not code-verified):**
- Rotate all exchange API keys via exchange dashboards
- Update `.env` and `engine/.env` with new keys
- Verify `git log --all -p -- '*.env'` shows no key history

**Files**: `.gitignore`, `.githooks/pre-commit`

### US-123: 전 엔드포인트 JWT 인증 강제 (Priority 1)

**Current unauthenticated endpoints:**

server.py short-path aliases (5 endpoints):
| Endpoint | Line | Action |
|----------|------|--------|
| `GET /metrics` | 150 | Add `require_auth` dependency |
| `GET /status` | 169 | Add `require_auth` dependency |
| `POST /kill` | 179 | Add `require_auth` dependency |
| `GET /strategies` | 190 | Add `require_auth` dependency |
| `POST /strategies/{id}/toggle` | 194 | Add `require_auth` dependency |

routes/strategies.py (3 endpoints):
| Endpoint | Line | Action |
|----------|------|--------|
| `GET /api/v1/strategies` | 40 | Add `dependencies=[Depends(require_auth)]` |
| `POST /api/v1/strategies/{id}/toggle` | 47 | Add `dependencies=[Depends(require_auth)]` |
| `POST /api/v1/strategies/{id}/config` | 76 | Add `dependencies=[Depends(require_auth)]` |

routes/risk.py (3 endpoints):
| Endpoint | Line | Action |
|----------|------|--------|
| `GET /api/v1/mode` | 44 | Add `dependencies=[Depends(require_auth)]` |
| `GET /api/v1/risk/metrics` | 56 | Add `dependencies=[Depends(require_auth)]` |
| `GET /api/v1/metrics` | 97 | Add `dependencies=[Depends(require_auth)]` |

**Implementation pattern:**
```python
# server.py — import at top
from src.api.auth import require_auth
from fastapi import Depends

# For inline routes in server.py, wrap with Depends
@app.get("/metrics", dependencies=[Depends(require_auth)])
async def short_metrics(): ...

# For router routes, add to decorator
@router.get("", dependencies=[Depends(require_auth)])
```

**Exception**: `/api/auth/login` stays public (login endpoint).
**Exception**: `/api/v1/health` stays public (Docker healthcheck).

**Tests**: Update `engine/tests/unit/test_api_auth.py`:
- Each endpoint returns 401 without token
- Each endpoint returns 200/expected with valid token

**Files**: `engine/src/api/server.py`, `engine/src/api/routes/strategies.py`, `engine/src/api/routes/risk.py`

### US-124: JWT 시크릿 강화 + prod fail-fast (Priority 2)

**Note**: US-105 (Phase J-EXT, passes:true) already implemented JWT_SECRET fail-fast for prod and bcrypt hashing. US-124 delta:
1. Verify US-105 regression: `ENGINE_ENV=prod` + default JWT_SECRET → RuntimeError (already done)
2. **New**: `ENGINE_ENV=prod` + bcrypt not installed → RuntimeError (no silent SHA-256 fallback)
3. **New**: `ENGINE_ENV=prod` + `DASHBOARD_PASSWORD` unset → RuntimeError
4. **New**: Remove any remaining hardcoded defaults in `main.py`

**Files**: `engine/src/api/auth.py`

**Implementation**:
```python
# In auth.py, around password verification:
if ENGINE_ENV in ("prod", "staging"):
    try:
        import bcrypt  # noqa: F401
    except ImportError:
        raise RuntimeError("bcrypt required in production — pip install bcrypt")
    if not os.environ.get("DASHBOARD_PASSWORD"):
        raise RuntimeError("DASHBOARD_PASSWORD must be set in production")
```

### US-125: Nginx IP whitelist + X-Forwarded-For 프록시 신뢰 (Priority 3)

**Files**: `infra/nginx/ip-whitelist.conf`, `engine/src/api/middleware.py`

1. `infra/nginx/ip-whitelist.conf`:
   - Remove `allow all;`
   - Keep only RFC-1918 private ranges + explicit allowed IPs:
   ```nginx
   allow 10.0.0.0/8;
   allow 172.16.0.0/12;
   allow 192.168.0.0/16;
   allow 127.0.0.1;
   deny all;
   ```

2. `engine/src/api/middleware.py` — `_get_client_ip()`:
   - Add `TRUSTED_PROXIES` env var (comma-separated IPs)
   - Only trust `X-Forwarded-For` if request comes from trusted proxy
   ```python
   TRUSTED_PROXIES = set(os.environ.get("TRUSTED_PROXIES", "127.0.0.1,10.0.0.0/8").split(","))

   def _get_client_ip(request: Request) -> str:
       client_ip = request.client.host if request.client else "unknown"
       if client_ip in TRUSTED_PROXIES:
           forwarded = request.headers.get("x-forwarded-for", "")
           if forwarded:
               return forwarded.split(",")[0].strip()
       return client_ip
   ```

### US-126: Redis 인증 + dangerous commands 비활성 (Priority 4)

**Files**: `docker-compose.yml`, `infra/redis/redis.conf`, `engine/src/infra/redis/client.py`

1. `infra/redis/redis.conf`:
   ```
   requirepass ${REDIS_PASSWORD}
   rename-command CONFIG ""
   rename-command FLUSHALL ""
   rename-command FLUSHDB ""
   rename-command DEBUG ""
   ```

2. `docker-compose.yml` — redis service:
   ```yaml
   environment:
     - REDIS_PASSWORD=${REDIS_PASSWORD}
   command: redis-server /usr/local/etc/redis/redis.conf
   ```

3. `engine/src/infra/redis/client.py`:
   - Add `password` parameter from `REDIS_PASSWORD` env var
   ```python
   redis_password = os.environ.get("REDIS_PASSWORD", "")
   self._client = redis.asyncio.Redis(host=host, port=port, password=redis_password, ...)
   ```

4. Update `engine/.env` and root `.env`:
   ```
   REDIS_PASSWORD=<generate-strong-password>
   ```

### US-127: CSP 헤더 강화 (Priority 5)

**Files**: `infra/nginx/nginx.conf`, `dashboard/next.config.js`

1. `infra/nginx/nginx.conf`:
   - Replace `unsafe-inline`/`unsafe-eval` with nonce-based CSP:
   ```nginx
   add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws: wss:; font-src 'self';";
   ```
   Note: `style-src 'unsafe-inline'` needed for Next.js styled-jsx. `script-src` drops `unsafe-eval`.

2. `dashboard/next.config.js`:
   ```javascript
   async headers() {
     return [{
       source: '/(.*)',
       headers: [{
         key: 'Content-Security-Policy',
         value: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws://localhost:* wss://localhost:*; img-src 'self' data:;"
       }]
     }]
   }
   ```

### US-128: pytest backoff 테스트 수정 (Priority 6)

**Files**: `engine/tests/unit/test_base_collector.py`

**Root cause**: `base_collector.py:_backoff()` added jitter (±25%) but test expects exact delay values.

**Fix**: Change assertion to accept range:
```python
async def test_backoff_doubles_delay_each_call(self):
    collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
    base_delays = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0]

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        for base in base_delays:
            await collector._backoff()
            actual = mock_sleep.call_args[0][0]
            # jitter ±25%: actual should be within [base*0.75, base*1.25]
            assert base * 0.75 <= actual <= base * 1.25, \
                f"Expected {base}±25%, got {actual}"
```

---

## 3. Team Assignment (Stage B)

| Member | Role | US | Files |
|--------|------|-----|-------|
| **Yujin** | executor | US-123, US-124 | `engine/src/api/server.py`, `routes/strategies.py`, `routes/risk.py`, `auth.py` |
| **Gaeul** | executor | US-125, US-126, US-127 | `infra/nginx/`, `docker-compose.yml`, `redis/`, `middleware.py`, `dashboard/next.config.js` |
| **Wonyoung** | test-engineer | US-128 + all auth tests | `tests/unit/test_base_collector.py`, `tests/unit/test_api_auth.py` |
| **Lead** | direct | US-152 | `.gitignore`, `.githooks/pre-commit` |

No Rei needed (no dashboard UI changes, only config).

---

## 4. Verification Criteria

| US | Test Command | Expected |
|----|-------------|----------|
| US-152 | `git diff --cached -- '*.env' && echo BLOCKED` | pre-commit blocks .env |
| US-123 | `pytest tests/unit/test_api_auth.py -x` | 11 new 401-without-token tests PASS |
| US-124 | `ENGINE_ENV=prod pytest tests/unit/test_api_auth.py -k prod` | RuntimeError assertions PASS |
| US-125 | `grep -c 'allow all' infra/nginx/ip-whitelist.conf` | 0 (removed) |
| US-126 | `grep requirepass infra/redis/redis.conf` | present |
| US-127 | `grep 'Content-Security-Policy' infra/nginx/nginx.conf` | present, no unsafe-eval |
| US-128 | `pytest tests/unit/test_base_collector.py -x` | 0 failures |
| **ALL** | `cd engine && python -m pytest tests/ -x --tb=short` | 0 failures (current 1 → 0) |

---

## 5. QUANT GATE

**Not required** — Phase S1 files contain no strategy/formula keywords (`slippage|signal|strategy|executor|funding|futures|triangular|statistical|friction|cost_calculator|regime|hmm|xgboost|onnx|dex|gas_oracle`).

---

## 6. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Auth on /metrics breaks Prometheus scraping | Prometheus scrapes via Docker internal network (no auth needed) OR add Prometheus bearer token |
| Auth on /status breaks Docker healthcheck | health.py `/api/v1/health` stays public (separate from `/status`) |
| Redis password breaks existing connections | Update ALL Redis clients (engine, monitoring, redis-exporter) simultaneously |
| CSP breaks Next.js functionality | Keep `style-src 'unsafe-inline'` for styled-jsx, test thoroughly |
| Nginx deny all blocks Docker services | Docker services use internal network, not Nginx proxy |

---

## 7. Dependencies

- No cross-Phase dependencies (S1 is independent)
- Internal: US-123 should complete before US-124 (auth patterns established first)
- US-152 is operational (can start immediately, independent of code changes)
