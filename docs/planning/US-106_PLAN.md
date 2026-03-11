# US-106: WebSocket 피드 JWT 인증

## 요약
현재 `/ws`, `/ws/feed`, `/ws/strategies` 3개 WebSocket 엔드포인트가 인증 없이 열려 있음.
JWT 토큰 검증을 추가하여 인증된 사용자만 WebSocket 연결 가능하도록 함.

## 현재 상태
- REST API: `require_auth` 의존성으로 JWT 검증 적용됨
- WebSocket: 인증 없음 — 누구나 연결 가능
- 대시보드: `leviathan_token` 쿠키에 JWT 저장, WS 연결 시 토큰 미전달

## 구현 계획

### 1. auth.py — `verify_ws_token()` 함수 추가
```python
def verify_ws_token(websocket: WebSocket) -> str | None:
    """WebSocket JWT 검증. ?token= 쿼리 파라미터 우선, 없으면 헤더 확인."""
    # 1) query param: ?token=xxx
    token = websocket.query_params.get("token")
    # 2) cookie fallback: leviathan_token (대시보드 호환)
    if not token:
        token = websocket.cookies.get("leviathan_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return str(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
```

### 2. server.py — 3개 WS 엔드포인트에 인증 추가
```python
from src.api.auth import verify_ws_token

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    user = verify_ws_token(websocket)
    if user is None:
        await websocket.close(code=4003, reason="Authentication required")
        return
    # ... 기존 로직
```

### 3. 대시보드 영향
- `useEngineWs()` 훅에서 WS 연결 시 `?token=` 쿼리 파라미터 추가 필요
- 단, 대시보드 수정은 US-106 범위 밖 (engine 보안만 담당)
- 쿠키 기반 fallback으로 기존 대시보드 호환성 유지

### 4. WebSocket Close Code
- 표준 WebSocket 프로토콜: 4000-4999 범위 = 애플리케이션 정의
- `4003`: 인증 필요 (HTTP 403에 대응)
- FastAPI WebSocket은 accept() 전 close() 불가 → accept() 후 즉시 close()

## 수용 기준
1. `/ws/feed` 연결 시 `?token=` 또는 쿠키 JWT 검증
2. 유효 토큰 없으면 WS 연결 즉시 종료 (code=4003)
3. 기존 WS 기능 정상 동작 (유효 토큰 시)

## 파일 변경
- `engine/src/api/auth.py`: `verify_ws_token()` 추가
- `engine/src/api/server.py`: 3개 WS 엔드포인트에 인증 적용
- `engine/tests/unit/test_api_server_routes.py`: WS 인증 테스트 추가

## QUANT GATE: 해당 없음 (전략/수식 키워드 미포함)
