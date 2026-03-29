# TF Semi-Final — 1시간 심화 검증

> QF PASS 후 호출. 완료 후 `/project:tf-final` 호출.

## 수행 (1시간)

### 1. 전략 수익성 심화
- 전략별 Sharpe ratio 계산
- 전략별 MDD 확인
- 수수료 대비 수익 검증

### 2. Live 전환 시뮬레이션
- EXECUTION_MODE=live 설정 시 동작 확인 (dry-run)
- 주문 생성 로직 검증 (실제 전송 X)
- 킬스위치 $50 한도 동작 확인

### 3. 문서 완전 동기화
- SSOT.md 최신화
- CLAUDE.md 최신화
- check_all 9/9

### 4. 1시간 Shadow 무중단

### 5. 판정
- PASS → "→ 다음: /project:tf-final"
- FAIL → Fix Loop → /project:sit3-audit
