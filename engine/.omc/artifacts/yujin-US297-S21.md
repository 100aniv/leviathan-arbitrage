# US-297: stat_arb WFE 음수 해결 — PASS

## 변경 파일
- `engine/config/strategy_params.json`: statistical_arb status "MONITOR" → "DISABLED", note 업데이트
- `engine/src/main.py`: StatisticalArbStrategy 등록 조건 추가 (status READY/MONITOR일 때만)

## 변경 내용
1. `strategy_params.json` statistical_arb 섹션:
   - `status`: "MONITOR" → "DISABLED"
   - `note`: "WFE=-1.03, disabled per S21 analysis. 9/97 folds positive only. Requires redesign before reactivation."

2. `main.py` _register_default_strategies():
   - StatisticalArbStrategy 등록을 `tuned.get("statistical_arb", {}).get("status") in ("READY", "MONITOR")` 조건부로 감쌈
   - 다른 전략들(sf_config, fr_config 등)과 동일한 패턴 적용

## 검증
- tests/unit/strategies/ + tests/unit/core/: 344 passed, 0 failed
- stat_arb는 이제 DISABLED 상태에서 strategy manager에 등록되지 않음
- SHADOW_DISABLED_STRATEGIES env var 없이도 shadow에서 제외됨

## 결정 근거
- WFE=-1.03: 97개 폴드 중 9개만 양수 (9.3%)
- 파라미터 조정으로 해결 불가 → 전략 비활성화가 최소 위험 경로
- 재활성화 조건: 전략 로직 재설계 + WFE > 0.3 달성 시
