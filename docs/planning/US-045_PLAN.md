# US-045: Scheduled Offline Tuner Docker 서비스

## Acceptance Criteria
1. engine/src/tuning/scheduled_tuner.py 생성
2. docker-compose.yml에 auto-tuner 서비스 추가
3. 매주 일요일 02:00 자동 실행 (APScheduler)
4. 전략별 독립 Optuna 최적화 (100 trials)
5. Telegram 결과 보고

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| engine/src/tuning/scheduled_tuner.py | NEW — 스케줄러 | Jennie |
| docker-compose.yml | EDIT — auto-tuner 서비스 | Jennie |
| engine/tests/unit/tuning/test_scheduled_tuner.py | NEW — 테스트 | Lisa |
