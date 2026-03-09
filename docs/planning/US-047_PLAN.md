# US-047: Adaptive Threshold + Regime Detector

## Acceptance Criteria
1. adaptive_threshold.py 생성: 매 1시간 MIN_EDGE 미세조정
2. WR < 50% → edge 상향, WR > 90% → edge 하향
3. regime_detector.py 생성: LOW/MEDIUM/HIGH/CRISIS 분류
4. CRISIS → KillSwitch 발동 확인
5. 변경 이력 TimescaleDB 저장

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/tuning/adaptive_threshold.py | NEW — 1시간 MIN_EDGE 조정 | Jennie |
| engine/src/tuning/regime_detector.py | NEW — 시장 체제 분류기 | Jennie |
| engine/tests/unit/tuning/test_adaptive_threshold.py | NEW | Lisa |
| engine/tests/unit/tuning/test_regime_detector.py | NEW | Lisa |
