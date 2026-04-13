# Phase K Batch 1+2 — US-361 + US-363 구현 결과

## 테스트 결과
**PASS** — 5387 passed, 12 skipped, 0 failed (6:00)

## 변경 파일

### US-361: BacktestResult meta + POST /api/backtest/start
- `engine/src/modes/backtest.py`
  - `BacktestResult` dataclass에 5 meta 필드 추가: `strategy_ids`, `exchange_ids`, `seed_capital`, `period_label`, `by_exchange`
  - `BacktestMode.__init__()` 파라미터 추가: `strategy_ids`, `seed_capital`
  - `run()` 내 result 생성 시 meta 필드 설정
- `engine/src/api/routes/backtest.py`
  - `GET /api/backtest/result` 응답에 5 meta 필드 추가
  - `POST /api/backtest/start` 엔드포인트 추가 (`BacktestStartRequest` Pydantic 모델)

### US-363: POST /api/paper/start
- `engine/src/api/routes/paper.py` (신규 생성)
  - `POST /api/paper/start` — `PaperStartRequest` 파라미터, session_id 생성, ctx.paper_session 저장
  - `GET /api/paper/result` — 현재 paper session 상태 반환
- `engine/src/api/server.py`
  - paper_router 등록

## 핵심 요약
- 이중 슬리피지 없음 (PaperExecutor 미변경)
- WIRING AC: paper.py router 생성→server.py 주입→요청 시 호출
- 기존 테스트 전부 통과 (5387/5387)
