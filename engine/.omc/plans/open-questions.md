# Open Questions

## Phase S13 - 2026-03-17
- [ ] US-226 CRITICAL 버그 5개의 정확한 목록 확인 필요 — TF SF 2차 FAIL 보고서에서 추출해야 함. Entry Gate에서 식별된 5개가 최종인지 확인
- [ ] US-227 4계층 중 L4 (ML anomaly)의 범위 결정 — 기존 `regime_detector.py`를 활용할지, 별도 anomaly detector를 만들지. ML 의존성 최소화 vs 정확도 트레이드오프
- [ ] US-232 PositionRegistry의 persistence 범위 — in-memory only vs Redis 백업. Shadow 모드에서는 in-memory 충분하나 Live 전환 시 필요
- [ ] US-237 대시보드 CSP 정책의 구체적 범위 — 현재 CSP 헤더 상태 확인 필요. strict CSP vs report-only 먼저 적용할지
- [ ] Batch 2의 US-221 + US-225가 `real_signal_producer.py`를 동시 수정 — 순차 작업 또는 영역 분리 확인 필요
