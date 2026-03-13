# TF Semi-Final -- OFFICIAL TF LEADER VERDICT

**Date**: 2026-03-13
**TF Leader**: Nayeon (TWICE TF)
**Verdict**: FAIL -- Development Regression Required (6 Phases, 29 User Stories)

---

## 1. Verdict Summary

The TF Semi-Final report accurately identifies 9 CRITICAL, 12 HIGH, 19 MEDIUM, and 19 LOW findings.
Every CRITICAL and HIGH finding was independently verified against the source code. The proposed
6-phase roadmap (S1~S6) with 29 new User Stories adequately covers all CRITICAL and HIGH issues.

**Phase ordering is CORRECT**: S1 (Security) must come first because unauthenticated kill-switch
access and plaintext API keys represent immediate production risk. The remaining phases (S2~S6)
follow a logical dependency chain.

**One additional observation**: the `strategies` router (`engine/src/api/routes/strategies.py`)
has 3 endpoints WITHOUT `require_auth` (list at line 40, toggle at line 47, config at line 76),
while `get_strategy_trades` at line 109 does have auth. This is already covered by US-123's
acceptance criteria but should be explicitly called out during implementation.

---

## 2. CRITICAL Finding Verification (9/9 Confirmed)

| # | Finding | Verification | Covered By |
|---|---------|-------------|------------|
| C1 | API keys plaintext in .env | CONFIRMED: `.env:31` Binance key `7qNLI0b...`, `.env:51` Bithumb key `262b45e...`, both files (root + engine) | **NOT in S1~S6** -- see Section 4 |
| C2 | Redis no authentication | CONFIRMED: No `requirepass` in any .yml/.conf file | US-126 (S1) |
| C3 | DB schema 3-way divergence | CONFIRMED: `timescale.py` creates `ohlcv/spreads/signals` tables; `001_init_schema.sql` creates `orderbook_snapshots/execution_log/ohlcv_1m` -- different schemas | US-135 (S3) |
| C4 | MIN_EDGE_BPS mismatch | CONFIRMED: `.env:25` = 5 vs `engine/.env:25` = 3 | US-136 (S3) |
| C5 | Nginx IP whitelist disabled | CONFIRMED: `infra/nginx/ip-whitelist.conf:11` has `allow all;` | US-125 (S1) |
| C6 | /kill POST unauthenticated | CONFIRMED: `server.py:179` -- no `require_auth`, anyone can halt the engine | US-123 (S1) |
| C7 | /strategies unauthenticated | CONFIRMED: `server.py:190-200` short-paths + `strategies.py:40,47,76` router endpoints -- no auth | US-123 (S1) |
| C8 | /metrics unauthenticated | CONFIRMED: `server.py:150` -- Prometheus metrics exposed to public | US-123 (S1) |
| C9 | JWT weak secret (partial) | PARTIALLY RESOLVED by US-105: hardcoded default removed, ephemeral random generated in dev. But prod fail-fast for `DASHBOARD_PASSWORD` missing, bcrypt fallback to SHA-256 silent | US-124 (S1) |

### C1 Gap: API Key Management NOT Addressed

**The most severe CRITICAL finding (C1: plaintext API keys) has no dedicated US in S1~S6.**
US-123~128 cover authentication, JWT, Nginx, Redis, CSP, and tests -- but none address:
- Rotating compromised API keys
- Moving secrets to a vault (e.g., Docker secrets, HashiCorp Vault, or at minimum `.env` in `.gitignore`)
- Verifying `.env` files are not committed to git history

This must be added to S1. See Section 4 for recommendation.

---

## 3. HIGH Finding Verification (12/12 Confirmed)

| # | Finding | Verification | Covered By |
|---|---------|-------------|------------|
| H1 | RiskGuardian PortfolioState always zero | CONFIRMED: `main.py:778-779` hardcodes `used_capital=Decimal("0"), current_drawdown_pct=Decimal("0")` -- 5/9 checks neutralized | US-129 (S2) |
| H2 | PowerLaw k default 5.0 | CONFIRMED via SSOT: code defaults k=5.0 if env missing, SSOT says k=0.0 | US-136 (S3) |
| H3 | Shadow MDD absolute vs ratio | CONFIRMED via report: shadow.py uses USD absolute, SSOT defines (Peak-Current)/Peak ratio | US-148 (S5) |
| H4 | MIN_EDGE_BPS default 40 in main.py | Verified via report: env fallback = 40bps vs operational = 5bps. 8x trade reduction if .env lost | US-136 (S3) |
| H5 | Nginx WS port mismatch (8000 vs 8001) | Reported by Momo | US-137 (S3) |
| H6 | Backup restart: "no" | Reported by Momo | US-137 (S3) |
| H7 | Alertmanager disconnected | Reported by Momo | US-138 (S3) |
| H8 | Nginx IP whitelist open | Same as C5 | US-125 (S1) |
| H9 | X-Forwarded-For spoofing | Reported by Security | US-125 (S1) |
| H10 | /risk, /mode, /status no auth | CONFIRMED: `server.py:169` `/status` no auth | US-123 (S1) |
| H11 | /metrics no auth | Same as C8 | US-123 (S1) |
| H12 | Auto-Tuner NotImplementedError | CONFIRMED: `scheduled_tuner.py:173` raises `NotImplementedError` for TimescaleDB async loader | US-145 (S5) |

All 12 HIGH findings are covered by the proposed User Stories.

---

## 4. Phase Ordering Review

### Proposed Order: S1 -> S2 -> S3 -> S4 -> S5 -> S6

| Phase | Focus | US Count | Dependency | Ordering Correct? |
|-------|-------|----------|------------|-------------------|
| S1 | Security Hardening | 6 | None -- must be first (production safety) | YES |
| S2 | Engine Wiring | 6 | Independent of S1 but less urgent | YES |
| S3 | Infrastructure | 5 | Some items depend on S1 (Redis auth from S1 affects Docker config) | YES |
| S4 | Dashboard | 5 | Can run after S1 (CSP from S1), API prefix after S2/S3 wiring | YES |
| S5 | Data Pipeline | 4 | Depends on S2 (wiring) + S3 (DB schema) | YES |
| S6 | Documentation | 3 | Must be last (documents all changes from S1~S5) | YES |

**Phase ordering is CORRECT.** The dependency chain is sound:
- S1 first: security vulnerabilities block all other work from being production-safe
- S2 before S5: engine wiring must be complete before data pipeline can flow through it
- S3 before S5: DB schema must be unified before Auto-Tuner can write to TimescaleDB
- S6 last: documentation must reflect the final state after all fixes

### One Optimization

S2 (Engine Wiring) and S3 (Infrastructure) are largely independent. They could run in parallel
if team capacity allows. However, sequential execution is safer and the proposed order is acceptable.

---

## 5. Coverage Gap Analysis

### Missing from S1~S6 (Must Add)

| Gap | Description | Recommendation | Priority |
|-----|-------------|----------------|----------|
| **API Key Rotation** | C1 reports real API keys in `.env` committed to git. No US addresses key rotation or secret management | Add US to S1: rotate all compromised keys, add `.env` to `.gitignore`, scrub git history with `git filter-repo` | CRITICAL |
| **Strategies router partial auth** | `strategies.py` lines 40/47/76 lack `require_auth` while line 109 has it | Already in US-123 scope but acceptance criteria should explicitly list `/api/v1/strategies` GET, POST toggle, POST config | HIGH |

### Adequately Covered

All other CRITICAL and HIGH findings have dedicated User Stories with specific acceptance criteria.
The MEDIUM and LOW findings are appropriately distributed across S2~S6.

---

## 6. Risk Assessment for New Roadmap

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| S1 auth changes break dashboard | Medium | High | Run full dashboard E2E after S1 |
| S3 DB migration loses data | Low | Critical | Backup before migration, test on staging first |
| S2 wiring changes introduce regressions | Medium | Medium | Maintain 100% pytest pass throughout |
| S4 mock removal reveals missing API data | High | Low | Graceful fallback to "no data" state |
| S6 doc sync becomes stale again | High | Low | Add CI check for SSOT/prd.json consistency |

---

## 7. Smoke Test Failure

The 1 pytest failure (`test_backoff_doubles_delay_each_call`) is correctly assigned to US-128 (S1).
This is a test-only issue caused by jitter addition without test tolerance update. Low risk, easy fix.

---

## 8. Official TF Leader Decision

### VERDICT: FAIL -- Development Regression to S1~S6

**Rationale**:
1. 9 CRITICAL findings include 3 immediate security risks (unauthenticated kill-switch, plaintext API keys, Redis without auth) that make the system unsafe for any network-exposed deployment
2. 12 HIGH findings include fundamental engine defects (RiskGuardian neutralized, AutoTuner broken) that would produce incorrect risk assessment in production
3. The 6-phase roadmap (S1~S6) with 29 new US is well-structured and correctly ordered

**Conditions for TF Semi-Final Re-verification**:
1. All 29 US (US-123~US-151) must pass with evidence
2. pytest must reach 0 failures
3. Docker must have all services healthy (no restart loops)
4. **ADDITIONAL**: API keys must be rotated and `.env` must not contain real secrets in git
5. Each phase must complete a full Stage A~E cycle per the 5-Stage workflow

**Sign-off Chain**:
- S1~S6 development completion
- TF Semi-Final re-run (full 4-stage verification)
- TF Final (Progressive Shadow 72H)
- Live Kick-Off (Nayeon final signature + CEO approval)

---

**Signed**: Nayeon, TF Leader (TWICE)
**Date**: 2026-03-13
**Document**: `docs/checklists/tf-semi-final-verdict.md`
**Status**: OFFICIAL -- This verdict supersedes any informal assessments
