#!/bin/bash
# Shadow Evidence Gate Hook — Shadow 결과 증거 파일 검증
# leviathan 워크플로우 실행 중일 때만 활성화.
# Phase 완료 시 shadow-result-latest.json이 존재하고 유효한지 확인.
# exit 2 = 차단 + 에이전트에 피드백 주입.

set -euo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '.')"

# Guard: leviathan 워크플로우 실행 중일 때만 활성화
# leviathan-progress.json이 없거나 next_stage가 없으면 = 일반 대화 → 스킵
PROGRESS_FILE="$ROOT/.omc/state/leviathan-progress.json"
if [ ! -f "$PROGRESS_FILE" ]; then
  exit 0  # leviathan 비활성 → 검증 스킵
fi

# next_stage가 "C" 이상일 때만 검증 (Stage B Shadow 완료 후)
STAGE=$(python3 -c "
import json
try:
    with open('$PROGRESS_FILE') as f:
        d = json.load(f)
    print(d.get('next_stage', ''))
except: pass
" 2>/dev/null)

if [ "$STAGE" != "C" ] && [ "$STAGE" != "complete" ]; then
  exit 0  # Stage B 이전 또는 비활성 → 검증 스킵
fi

SHADOW_FILE="$ROOT/.omc/state/shadow-result-latest.json"

ERRORS=""

# Check 1: 파일 존재
if [ ! -f "$SHADOW_FILE" ]; then
  ERRORS="${ERRORS}\n- Shadow 결과 파일 없음. shadow-tester(Minji)가 결과를 기록하지 않았음."
  ERRORS="${ERRORS}\n- 필수: .omc/state/shadow-result-latest.json"
  echo -e "=== Shadow Evidence Gate BLOCKED ===$ERRORS" >&2
  exit 2
fi

# Check 2: 파일이 10분 이내에 생성되었는지 (stale 방지)
FILE_AGE=$(python3 -c "
import os, time
mtime = os.path.getmtime('$SHADOW_FILE')
age_min = (time.time() - mtime) / 60
print(f'{age_min:.1f}')
" 2>/dev/null)

if [ -n "$FILE_AGE" ]; then
  IS_STALE=$(python3 -c "print('stale' if float('$FILE_AGE') > 60 else '')" 2>/dev/null)
  if [ -n "$IS_STALE" ]; then
    ERRORS="${ERRORS}\n- Shadow 결과가 ${FILE_AGE}분 전 데이터 (60분 초과 = stale). 재실행 필요."
  fi
fi

# Check 3: JSON 유효성 + 필수 필드
VALIDATION=$(python3 -c "
import json, sys
try:
    with open('$SHADOW_FILE') as f:
        d = json.load(f)
    required = ['total_pnl', 'total_trades', 'crash_count', 'by_strategy']
    missing = [k for k in required if k not in d]
    if missing:
        print(f'missing_fields:{','.join(missing)}')
    elif d.get('total_trades', 0) == 0:
        print('zero_trades')
    elif d.get('runtime_seconds', 0) < 580:
        print(f'short_runtime:{d.get(\"runtime_seconds\", 0)}s')
except json.JSONDecodeError:
    print('invalid_json')
except Exception as e:
    print(f'error:{e}')
" 2>/dev/null)

if [ -n "$VALIDATION" ]; then
  case "$VALIDATION" in
    missing_fields:*)
      ERRORS="${ERRORS}\n- Shadow 결과 필수 필드 누락: ${VALIDATION#missing_fields:}"
      ;;
    zero_trades)
      ERRORS="${ERRORS}\n- Shadow 결과: 0 trades. 엔진이 실행되지 않았거나 전략 미활성."
      ;;
    short_runtime:*)
      ERRORS="${ERRORS}\n- Shadow 실행 시간 부족: ${VALIDATION#short_runtime:} (최소 580초 = ~10분)"
      ;;
    invalid_json)
      ERRORS="${ERRORS}\n- Shadow 결과 파일이 유효한 JSON이 아님."
      ;;
    error:*)
      ERRORS="${ERRORS}\n- Shadow 결과 파싱 에러: ${VALIDATION#error:}"
      ;;
  esac
fi

if [ -n "$ERRORS" ]; then
  echo -e "=== Shadow Evidence Gate BLOCKED ===$ERRORS" >&2
  echo -e "\nShadow 10분+ 재실행 후 결과 파일 기록 필요." >&2
  exit 2
fi

echo "Shadow Evidence Gate PASS" >&2
exit 0
