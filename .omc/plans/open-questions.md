# Open Questions

## Phase S3 Infrastructure Hardening - 2026-03-14

- [ ] 엔진 WS가 FastAPI 내부(포트 8000)에서 서빙되는지, 별도 uvicorn(포트 8001)에서 서빙되는지 코드 확인 필요 — Nginx proxy_pass 포트 결정에 영향 (US-137)
- [ ] Alertmanager Telegram 환경변수를 docker-compose.yml environment로 전달할 때 `${}` 치환이 정상 동작하는지 확인 — alertmanager.yml 내 env var 참조 방식이 Alertmanager 네이티브 문법과 호환되어야 함 (US-138)
- [ ] db-backup/wal-backup을 restart: unless-stopped로 변경하면 스크립트 종료 후 즉시 재시작됨 — 의도된 동작인지, sleep 루프가 필요한지 결정 필요 (US-137)
- [ ] ohlcv (timescale.py, time 컬럼)와 ohlcv_1m (migrations/001, ts 컬럼) 간의 관계 정리 — 동일 데이터의 중복 테이블인지, 각각 raw vs aggregated인지 확인 필요 (US-135)

## Phase S6 Documentation Sync - 2026-03-14

- [ ] prd.json `dashboard/src/pages/*.tsx` 경로들이 App Router 마이그레이션 후 실제로 존재하지 않을 수 있음 — 검증 스크립트로 전수 확인 필요 (US-149)
- [ ] Upbit 수수료를 CLAUDE.md "자주 틀리는 패턴"에 추가할지, SSOT 참조로 충분한지 결정 필요 — 세션마다 Upbit Maker 0.05% vs Taker 0.139% 혼동 빈도에 따라 판단 (US-150)
- [ ] SSOT.md RiskGuardian Check #4가 두 가지 역할(CircuitBreaker + net_exposure)을 수행 — 번호를 분리할지(#4a/#4b) 또는 현행 유지할지 결정 필요 (US-151)
- [ ] prd.json의 US-150 AC에 "3,747→실제 수"라고 되어 있으나 실제 현재 수는 4,460 — AC 자체도 현행화 필요 (US-150)

## Phase S15 CRITICAL + ML 연결 - 2026-03-19
- [ ] US-257: `_stats.total_profit`/`_stats.total_loss` 필드가 ShadowMode._stats에 이미 존재하는지 확인 필요 — 없으면 on_fill()에서 축적 로직 추가 필요
- [ ] US-247: estimate_cost()에 rollback 비용 추가 시 avg_rollback_cost 파라미터를 어디서 가져올지 — CostCalculator 내부 trade_history 사용 vs 외부 주입
- [ ] US-248: ADV 동적 계산의 시간 윈도우 결정 — 5분 vs 15분 vs 1시간 (시장 특성에 따라 다름)
- [ ] US-254: RegimeDetector 인터페이스를 Protocol로 정의할지 ABC로 정의할지 — 6개 전략 공통 인터페이스
- [ ] US-256: peak_equity DB 테이블 스키마 — 기존 shadow_metrics 테이블 확장 vs 신규 테이블
- [ ] US-258-a: ShadowMiniTuner를 활성화할지 제거할지 — TF SF Stage 3 결과(PROVEN/NEUTRAL/HARMFUL) 기반 판단 필요, 현재 데이터 없음
- [ ] US-253: MLCanary 초기 단계를 DISABLED로 시작할지 SHADOW로 시작할지 — ONNX 모델 미존재 시 DISABLED 강제가 안전
