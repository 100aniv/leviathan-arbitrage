# Open Questions

## Phase S13 - 2026-03-17
- [ ] US-226 CRITICAL 버그 5개의 정확한 목록 확인 필요 — TF SF 2차 FAIL 보고서에서 추출해야 함. Entry Gate에서 식별된 5개가 최종인지 확인
- [ ] US-227 4계층 중 L4 (ML anomaly)의 범위 결정 — 기존 `regime_detector.py`를 활용할지, 별도 anomaly detector를 만들지. ML 의존성 최소화 vs 정확도 트레이드오프
- [ ] US-232 PositionRegistry의 persistence 범위 — in-memory only vs Redis 백업. Shadow 모드에서는 in-memory 충분하나 Live 전환 시 필요
- [ ] US-237 대시보드 CSP 정책의 구체적 범위 — 현재 CSP 헤더 상태 확인 필요. strict CSP vs report-only 먼저 적용할지
- [ ] Batch 2의 US-221 + US-225가 `real_signal_producer.py`를 동시 수정 — 순차 작업 또는 영역 분리 확인 필요

## Phase I (US-344~350) - 2026-04-01
- [ ] US-348c BookWalkSlippage 처리 방향 — 옵션 A(LiveMode PaperExecutor 연결) vs 옵션 B(DEPRECATED 처리). `risk/slippage.py:77` 의존성 확인 후 구현 팀이 결정 필요 — 이중 슬리피지 방지 규칙과 충돌 없음 확인 전제
- [ ] US-350 OrangeX WS 엔드포인트 — 공개 문서 없음. exa.ai `site:orangex.com api websocket` 검색 또는 사장님 계정에서 공식 문서 URL 확인 필요 — 어댑터 개발 착수 전 해결 필수
- [ ] US-345 config/settings.toml 실제 존재 여부 — 탐색 시 `engine/config/` 에서 shadow_mode.json 등 확인됨, settings.toml은 미확인. 존재 시 engine.json으로 이전 후 삭제 필요 — 설정 통합 완료 기준에 영향
- [ ] US-350 Gate.io / Bitget / OKX API 키 — 사장님이 `engine/.env`에 직접 입력 필요 (`GATEIO_API_KEY`, `GATEIO_API_SECRET`, `BITGET_API_KEY`, `BITGET_API_SECRET`). 개발 팀 대기 상태 — 입력 후 `exchanges.active` 배열 추가 진행
- [ ] US-348b TCAAnalyzer 연결 방식 — `_record_trade_to_db()` 내 직접 호출 vs executor `on_execution_complete` 콜백 DI. `main.py` executor DI 패턴 확인 후 결정 — 어느 방식이든 모든 체결 경로(성공/실패/부분) 커버 필수

## Phase J (US-351~356) - 2026-04-01
- [ ] orderbook_snapshots 실제 레코드 수 확인 — Shadow canary 실행 이력 기반으로 충분한지 불확실. US-356 통합 검증 전 `SELECT COUNT(*) FROM orderbook_snapshots` 직접 확인 필요 — 부족 시 Shadow 추가 실행 후 Phase J 착수
- [ ] LiveGate WFA 타입 호환성 — `live_gate_continuous.py:94` 의 `result.walk_forward` 필드가 `analysis/walk_forward.py` `WalkForwardResult` vs `analysis/ml_backtest.py` `ABTestResult` 중 어느 타입 기대하는지 US-356 착수 전 확인 필요
- [ ] US-353 `/api/backtest/wfa` 라우터 위치 — `engine/src/api/routes/` 내 기존 파일에 추가할지 신규 `backtest.py` 생성할지 executor 팀 결정 필요
