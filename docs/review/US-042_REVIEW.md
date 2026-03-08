# US-042 Code Review: Telegram 인프라 모니터링

**Date**: 2026-03-09
**Reviewer**: code-reviewer (opus)
**Verdict**: **NEEDS FIX**

## Files Reviewed

| # | File | Type | Lines |
|---|------|------|-------|
| 1 | `engine/src/infra/monitor_daemon.py` | NEW | 162 |
| 2 | `docker-compose.yml` | EDIT | +33 (monitoring service) |
| 3 | `engine/tests/unit/infra/test_monitor_daemon.py` | NEW | 273 |

## Verification

| Check | Result |
|-------|--------|
| pytest | 16 PASS, 0 failures (2.20s) |
| docker-compose valid | YES (`docker compose config --quiet` exit 0) |
| LSP diagnostics (monitor_daemon.py) | 0 errors |
| LSP diagnostics (test_monitor_daemon.py) | 0 errors |
| Hardcoded secrets scan | CLEAN (no tokens/keys in source) |

## Spec Compliance (Stage 1) — PASS

| Requirement | Status |
|-------------|--------|
| 5분 주기 헬스체크 데몬 | OK — `interval_sec=300` default, env-configurable |
| Redis PING 체크 | OK — `redis.asyncio` with 5s timeout |
| TimescaleDB SELECT 1 체크 | OK — `asyncpg` with 10s timeout |
| Engine /health HTTP 체크 | OK — `httpx.AsyncClient` with 10s timeout |
| 연속 실패 카운트 + threshold 알림 | OK — `failure_counts` dict, default threshold=3 |
| 복구 알림 | OK — `_handle_recovery` sends on transition |
| Telegram 알림 발송 | OK — uses existing `TelegramAlerter` |
| Docker 컨테이너 구성 | OK — reuses `leviathan-engine` image |
| 단위 테스트 | OK — 16 tests covering all methods |

## Issues Found

### [HIGH] Double-counting failure in `check_all()` + individual checks

**File**: `engine/src/infra/monitor_daemon.py:74-75, 91-93, 114-116, 132-134`

**Issue**: Each individual check method (`check_redis`, `check_timescaledb`, `check_engine`) calls `_handle_failure()` internally in its `except` block. Then `check_all()` also calls `_handle_failure()` when the check returns `False`. This increments the failure counter **twice** per actual failure.

**Impact**: `failure_threshold=3` is effectively `2` — alerts fire after 2 real failures instead of 3.

**Evidence**:
```
check_redis() except → _handle_failure("redis", str(exc))  → count = 1
check_redis() returns False
check_all()   sees False → _handle_failure("redis", "...")  → count = 2  ← DOUBLE
```

**Fix**: Remove `_handle_failure()` calls from lines 93, 116, 134 in individual check methods. Let `check_all()` be the single owner of failure tracking.

---

### [HIGH] Resource leak in `check_redis()` — client not closed on failure

**File**: `engine/src/infra/monitor_daemon.py:87-94`

**Issue**: `aioredis.from_url()` creates a client (line 87), but if `ping()` raises (line 88), `aclose()` (line 89) is never called. The Redis connection is leaked.

**Fix**: Use try/finally:
```python
client = aioredis.from_url(url, socket_connect_timeout=5)
try:
    await client.ping()
    return True
except Exception as exc:
    logger.warning("redis_check_failed", error=str(exc))
    return False
finally:
    await client.aclose()
```

---

### [HIGH] Resource leak in `check_timescaledb()` — connection not closed on failure

**File**: `engine/src/infra/monitor_daemon.py:110-117`

**Issue**: `asyncpg.connect()` creates a connection (line 110), but if `fetchval()` raises (line 111), `conn.close()` (line 112) is never called. The DB connection is leaked.

**Fix**: Use try/finally:
```python
conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=10)
try:
    await conn.fetchval("SELECT 1")
    return True
except Exception as exc:
    logger.warning("timescaledb_check_failed", error=str(exc))
    return False
finally:
    await conn.close()
```

---

### [MEDIUM] No-op Docker healthcheck for monitoring service

**File**: `docker-compose.yml:244`

**Issue**: `python -c "import sys; sys.exit(0)"` always returns 0 regardless of daemon state. It only verifies Python is installed, not that the monitor loop is running.

**Fix**: Use a file-based heartbeat — write a timestamp in the daemon loop, then check staleness:
```yaml
# In daemon: write heartbeat file each cycle
# Healthcheck:
test: ["CMD", "python", "-c", "import os,time; f='/tmp/monitor_heartbeat'; exit(0 if os.path.exists(f) and time.time()-os.path.getmtime(f)<600 else 1)"]
```

---

### [MEDIUM] Unbounded alert spam after threshold

**File**: `engine/src/infra/monitor_daemon.py:142-146`

**Issue**: Once `failure_counts[service] >= threshold`, **every** subsequent failure sends a Telegram alert. If Redis is down for 1 hour (12 checks at 5min interval), that's 10 duplicate CRITICAL alerts after threshold.

**Fix**: Only alert when count equals threshold, or add a cooldown multiplier:
```python
if count == self.threshold:  # == instead of >=
    await self.alerter.send_alert(...)
```
Or send reminders at exponential intervals (e.g., 3, 6, 12, 24...).

---

### [LOW] Missing `__init__.py` in `engine/tests/unit/infra/`

**File**: `engine/tests/unit/infra/` (directory)

**Issue**: No `__init__.py` found. Tests pass due to pytest rootdir config, but explicit package marker is standard practice for consistency with other test directories.

**Fix**: `touch engine/tests/unit/infra/__init__.py`

---

### [LOW] Missing `__init__.py` in `engine/src/infra/`

**File**: `engine/src/infra/` (directory)

**Issue**: Glob finds no `__init__.py`. Module resolution works via namespace packages but explicit init file is more robust.

**Fix**: `touch engine/src/infra/__init__.py` (if not relying on namespace packages intentionally)

---

## Summary

| Severity | Count | Details |
|----------|-------|---------|
| CRITICAL | 0 | — |
| HIGH | 3 | Double-counting failure, Redis leak, DB leak |
| MEDIUM | 2 | No-op healthcheck, alert spam |
| LOW | 2 | Missing `__init__.py` (x2) |
| **Total** | **7** | |

## Verdict: NEEDS FIX

3 HIGH issues must be resolved before merge:

1. **Double-counting** — Remove `_handle_failure()` from individual check methods (3 deletions)
2. **Redis resource leak** — Add try/finally around ping/aclose
3. **DB resource leak** — Add try/finally around fetchval/close

MEDIUM issues (healthcheck, alert spam) are strongly recommended but not blocking.

## Positive Notes

- Clean separation of concerns — daemon, alerter, checks are well-structured
- Graceful optional dependency handling (`_REDIS_AVAILABLE`, `_ASYNCPG_AVAILABLE`, `_HTTPX_AVAILABLE`)
- Good test coverage (16 tests, all methods covered)
- Recovery notification logic is correct
- Docker service reuses existing engine image (no image bloat)
- No hardcoded secrets — all config via env vars
- `postgresql+asyncpg://` → `postgresql://` DSN conversion is a nice touch
