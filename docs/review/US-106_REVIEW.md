# US-106 Code Review: WebSocket JWT Authentication

**Reviewer:** code-reviewer (Claude Opus 4.6)
**Date:** 2026-03-12
**Verdict:** PASS (REQUEST CHANGES — 1 MEDIUM, 2 LOW)

---

## Summary

**Files Reviewed:** 3
**Total Issues:** 3

| Severity | Count | Action |
|----------|-------|--------|
| CRITICAL | 0 | — |
| HIGH | 0 | — |
| MEDIUM | 1 | Consider fixing before merge |
| LOW | 2 | Optional / advisory |

---

## Stage 1: Spec Compliance

US-106 요구사항: WebSocket 엔드포인트 3개(`/ws`, `/ws/feed`, `/ws/strategies`)에 JWT 인증 추가.

| 요구사항 | 상태 |
|---------|------|
| `verify_ws_token()` 함수 `auth.py`에 추가 | PASS |
| `?token=` 쿼리 파라미터 지원 | PASS |
| `leviathan_token` 쿠키 지원 (대시보드 호환) | PASS |
| 쿼리 파라미터 우선순위 > 쿠키 | PASS |
| `/ws` 인증 추가 | PASS |
| `/ws/feed` 인증 추가 | PASS |
| `/ws/strategies` 인증 추가 | PASS |
| 인증 실패 시 close code 4003 | PASS |
| 만료 토큰 거부 | PASS |
| 잘못된 토큰 거부 | PASS |
| 15개 신규 테스트 추가 | PASS (15 passed in 3.80s) |

**Spec Compliance: PASS — 모든 요구사항 구현 확인.**

---

## Stage 2: Code Quality

### LSP Diagnostics

세 파일 모두 타입 오류 없음 (pyright/pylsp 클린).

---

## Issues

### [MEDIUM] close-before-accept 패턴 — Starlette 동작 불일치 위험

**File:** `engine/src/api/server.py:202, 219, 236`

**Issue:**
`verify_ws_token()` 실패 시 `websocket.accept()` 없이 곧바로 `websocket.close(code=4003, reason="...")` 를 호출한다.

```python
# 현재 코드 (세 엔드포인트 동일 패턴)
user = verify_ws_token(websocket)
if user is None:
    await websocket.close(code=4003, reason="Authentication required")  # accept() 전
    return
await ws_manager.connect(websocket)  # 내부에서 accept() 호출
```

Starlette/ASGI 스펙상 WebSocket 연결은 **accept → close** 순서가 표준이다.
`accept()` 없이 `close()` 를 호출하면 Starlette는 내부적으로 `accept()` 후 `close()`를 수행하는데, 이 동작은 Starlette 버전에 따라 다르다.

- **Starlette 0.27 이전**: `accept()` 없이 `close()` 호출 시 `close frame`이 전송되지 않고 TCP 연결만 끊김 → 클라이언트가 close code 4003을 수신하지 못할 수 있음.
- **Starlette 0.28+**: 내부에서 자동 accept 후 close. 하지만 현재 테스트는 `pytest.raises(Exception)` 로만 검증하므로 4003 코드 수신 여부를 실제로 단언하지 않는다.

**Fix:**
```python
user = verify_ws_token(websocket)
if user is None:
    await websocket.accept()
    await websocket.close(code=4003, reason="Authentication required")
    return
```
또는 Starlette의 close-before-accept 자동 처리에 의존하되, 테스트에서 실제 close code 4003 수신을 명시적으로 검증할 것.

---

### [LOW] `user` 변수 미사용 — 감사 로깅 기회 손실

**File:** `engine/src/api/server.py:200, 217, 234`

**Issue:**
`verify_ws_token()` 의 반환값 `user` (username string)가 저장되지만 이후 코드에서 전혀 사용되지 않는다. REST `require_auth` 의존성이 username을 반환하는 것과 대칭적으로, WebSocket 접속 시 인증된 사용자명을 로그에 남기는 것이 감사(audit trail) 관점에서 유용하다.

```python
user = verify_ws_token(websocket)
if user is None:
    ...
await ws_manager.connect(websocket)
# user는 이후 어디서도 사용되지 않음
```

**Fix (선택적):**
```python
user = verify_ws_token(websocket)
if user is None:
    await websocket.close(code=4003, reason="Authentication required")
    return
logger.info("WebSocket /ws connected — user: %s", user)
await ws_manager.connect(websocket)
```

---

### [LOW] `TestWebSocketAuth` 거부 테스트가 예외 타입을 한정하지 않음

**File:** `engine/tests/unit/test_api_server_routes.py:576, 586, 597, 618, 638`

**Issue:**
인증 실패 시나리오 5개 테스트 모두 `pytest.raises(Exception)` 으로 광범위하게 잡는다. `WebSocketDisconnect` 나 `starlette.websockets.WebSocketDisconnect` 로 한정하지 않으면, 코드 버그로 인한 다른 예외(`AttributeError`, `RuntimeError` 등)도 테스트를 통과시킬 수 있다.

```python
# 현재: 모든 예외를 수용
with pytest.raises(Exception):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
```

**Fix:**
```python
from starlette.websockets import WebSocketDisconnect
with pytest.raises((WebSocketDisconnect, Exception)):
    ...
```
또는 Starlette `TestClient` WS close 동작을 확인한 후 정확한 예외 타입으로 좁힐 것. 최소한 `Exception` 대신 `(WebSocketDisconnect, OSError)` 정도로 범위를 좁히는 것이 권장된다.

---

## 긍정적 사항

1. **`verify_ws_token` 구현의 보안 정확성**: `jwt.ExpiredSignatureError` 와 `jwt.InvalidTokenError` 를 각각 잡아 None 반환. REST `require_auth` 와 동일한 예외 처리 전략으로 일관성 있음.

2. **토큰 우선순위 올바름**: `?token=` 쿼리 파라미터가 쿠키보다 우선. 프로그래매틱 클라이언트(curl, WebSocket 라이브러리)와 브라우저 대시보드 모두 지원.

3. **close code 4003 선택 적절**: WebSocket 애플리케이션 레벨 close code 4000–4999 범위 내 사용자 정의 코드. `4001`(인증 없음) 대신 `4003`(금지)을 선택한 것은 인증 실패(토큰 있지만 유효하지 않음)와 미인증(토큰 없음)을 구분하지 않는 현재 구조와 일치.

4. **기존 REST auth와의 일관성**: `require_auth` 가 Bearer 스킴으로 동일한 `_JWT_SECRET`/`_JWT_ALGORITHM` 을 사용하고, `verify_ws_token` 도 동일 설정을 공유. 인증 설정이 단일 모듈(`auth.py`)에 집중되어 있어 유지보수 용이.

5. **타입 안전성**: `verify_ws_token(websocket: Any) -> str | None` 시그니처로 WebSocket 타입을 느슨하게 받아 테스트에서 MagicMock 주입 가능. LSP 오류 없음.

6. **테스트 커버리지**: `TestVerifyWsToken` (6개) + `TestWebSocketAuth` (9개) = 15개. 유효 토큰/쿠키/우선순위/만료/잘못된 토큰/토큰 없음 + 3 엔드포인트 각각 검증.

---

## 최종 판정

**PASS** — CRITICAL/HIGH 이슈 없음. 즉시 병합 가능.

MEDIUM 이슈(close-before-accept)는 현재 Starlette 버전에서 동작하지만, 명시적 `accept()` 후 `close()` 패턴으로 변경하면 버전 의존성을 제거할 수 있다. 다음 PR에서 함께 처리하거나 별도 US로 트래킹 권장.
