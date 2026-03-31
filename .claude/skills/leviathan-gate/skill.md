---
name: leviathan-gate
description: "LEVIATHAN Phase 전환 게이트 강제. 물리적 증거 파일 없으면 다음 단계 진행 차단. 'gate check', 'Phase 전환', '증거 확인', 'passes:true 검증', 'check_all', 'Entry Gate', 'LiveGate'가 언급되면 반드시 이 스킬을 사용할 것."
---

# LEVIATHAN Gate Enforcement (gstack 철학 적용)

게이트 미충족 시 **멈춤 허용**. "완료"는 코드 존재가 아닌 런타임 증거로만 판정.

## Gate 체크 순서 (단계별)

### Entry Gate (Stage A → B 진입 전)

```bash
# 1. check_all 실행
cd engine && python -m src.workflow.cli check_all
# 목표: 9/9 OK

# 2. PRD 상태 확인
python -c "
import json
prd = json.load(open('.omc/prd.json'))
false_items = [us for us in prd['stories'] if not us.get('passes')]
print(f'passes:false: {len(false_items)}개')
for us in false_items[:5]:
    print(f'  - {us[\"id\"]}: {us[\"title\"]}')
"
```

**차단 조건**: check_all < 9/9 OR 해당 Phase 선행 US passes:false

### Assembly Gate (C-Step 1)

물리적 증거 파일 필수:
```bash
# 조립 검증 결과 파일 확인
cat .omc/state/assembly-gate-*.json | python -m json.tool
# "overall": "PASS" 필요
```

**차단 조건**: assembly-gate 파일 없음 OR overall ≠ PASS

### Shadow Gate (B-Step 2)

Shadow 10분 실행 증거 필수:
```bash
# Shadow 결과 파일 확인
cat .omc/state/shadow-result-*.json | python -m json.tool
# 13항목 모두 PASS + crash=0 + PnL>=0 필요

# 또는 로그 직접 확인
ls -lt engine/logs/ | head -5
grep -c "ERROR\|CRITICAL" engine/logs/$(ls -t engine/logs/ | head -1) || echo "오류 없음"
```

**차단 조건**: shadow-result 파일 없음 OR PnL < 0 OR crash > 0

### Code Review Gate (C-Step 2~3)

```bash
# 코드리뷰 결과 파일 확인
cat .omc/state/review-gate-*.json | python -m json.tool
# "must_fix_count": 0 필요
```

**차단 조건**: must_fix_count > 0 OR quorum 지적 미해결

### Release Gate (C-Step 5~6)

```bash
# Phase 완료 7항목 최종 확인
cd engine

# 1. 테스트
python -m pytest tests/ --co -q 2>/dev/null | tail -1

# 2. check_all
python -m src.workflow.cli check_all

# 3. PRD 전체 확인
python -c "
import json
prd = json.load(open('../.omc/prd.json'))
total = len(prd['stories'])
passed = sum(1 for us in prd['stories'] if us.get('passes'))
print(f'PRD: {passed}/{total} passes:true')
"
```

**차단 조건**: 7항목 중 1개라도 RED

### LiveGate (Shadow → Live 전환)

```bash
# LiveGate 6-check
python -c "
import json, os
checks = {
    '1_shadow_72h': os.path.exists('.omc/state/shadow-72h-complete.json'),
    '2_pnl_positive': True,  # shadow-result 확인
    '3_crash_zero': True,    # shadow-result 확인
    '4_all_us_pass': True,   # PRD 확인
    '5_tf_final_pass': os.path.exists('.omc/state/tf-final-pass.json'),
    '6_capital_limit': True  # config/engine.json 확인
}
failed = [k for k,v in checks.items() if not v]
print('PASS' if not failed else f'FAIL: {failed}')
"
```

**차단 조건**: 6-check 중 1개라도 FAIL → Live 전환 완전 차단

## Gate FAIL 시 행동 원칙

1. **멈춤** — 증거 없이 다음 단계 진행 금지
2. **원인 파악** — 어떤 항목이 왜 실패했는지 구체적으로 명시
3. **에스컬레이션** — 해당 팀 에이전트에게 수정 요청
4. **재검증** — 수정 완료 후 동일 Gate 재실행 (이전 결과 재사용 금지)

## 거짓 양성 방지 원칙

- `passes:true` 선언 = 런타임 호출 증거(로그/메트릭/체결 기록) 필수
- 증거 파일 경로를 PR 코멘트 또는 `.omc/state/`에 명시
- dead code (정의만 있고 호출 안 됨) = `passes:false` 유지
