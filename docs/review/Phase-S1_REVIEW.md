# Phase S1 Security Hardening — Code Review Summary

**Date**: 2026-03-13
**Reviewers**: Jennie (code-reviewer/opus), Lisa (critic/opus), Rose (quality-reviewer/sonnet), Jisoo (security-reviewer/sonnet)
**Scope**: US-123~128, US-152 (7 User Stories)
**Tests**: 4240 passed, 0 failures

## Review Verdict: PASS (after fixes)

### CRITICAL/HIGH Issues Found & Fixed

| # | Severity | Issue | File | Fix |
|---|----------|-------|------|-----|
| 1 | CRITICAL | `/api/v1/status` missing `require_auth` | `routes/trading.py:150` | Added `dependencies=[Depends(require_auth)]` |
| 2 | HIGH | Redis `requirepass` shell var interpolation | `infra/redis/redis.conf` | Moved to docker-compose `--requirepass` CLI flag |
| 3 | HIGH | Monitor daemon missing Redis password | `src/infra/monitor_daemon.py:85` | Added `REDIS_PASSWORD` env var to `from_url()` |
| 4 | MEDIUM | Prometheus `/metrics` breaks with JWT | `src/api/server.py:150` | Removed auth (IP-restricted at Nginx) |

### Pre-existing Issues Deferred to New US

| # | Severity | Issue | Recommendation |
|---|----------|-------|----------------|
| 5 | HIGH | Docker ports bound to `0.0.0.0` | Bind internal services to `127.0.0.1` |
| 6 | HIGH | Hardcoded DB credentials in docker-compose | Use `${VAR:?}` pattern |
| 7 | HIGH | CORS `allow_methods=["*"]` with credentials | Enumerate explicit methods |
| 8 | HIGH | JWT in WebSocket query parameter (logged) | Use cookie/subprotocol only |
| 9 | MEDIUM | CSP `connect-src` hardcoded to localhost | Make configurable for production |
| 10 | MEDIUM | Rate limiter not shared across workers | Migrate to Redis backend |
| 11 | MEDIUM | `/health` exposes `kill_switch_active` | Reduce to minimal `{"status":"ok"}` |
| 12 | MEDIUM | ALLOWED_IPS lacks CIDR support | Apply ipaddress.ip_network logic |
| 13 | LOW | JWT 24h expiry too long for finance | Reduce to 1h with refresh flow |
| 14 | LOW | Pre-commit hook misses .pem/.key patterns | Expand regex |

### Positive Observations
- Consistent `Depends(require_auth)` pattern across all routes
- Proper bcrypt + SHA-256 fallback with prod fail-fast
- Trusted proxy CIDR validation using `ipaddress` stdlib
- Redis dangerous commands (CONFIG/FLUSHALL/FLUSHDB/DEBUG) blocked
- CSP `unsafe-eval` successfully removed from both Nginx and Next.js
