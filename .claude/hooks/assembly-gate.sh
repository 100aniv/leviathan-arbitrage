#!/bin/bash
# Assembly Gate Hook — 물리적 차단
# Stage C 진입 시 조립 검증. exit 2 = 차단 + 에이전트에 피드백 주입.
# 설정: settings.local.json의 hooks.SubagentStop 또는 수동 호출

set -euo pipefail
cd "$(git rev-parse --show-toplevel)/engine" 2>/dev/null || exit 0

ERRORS=""

# Gate 1: pytest 전체 통과
if ! python -m pytest tests/ -x --tb=line -q --no-header 2>/dev/null | tail -1 | grep -q "passed"; then
  ERRORS="${ERRORS}\n- pytest FAIL: 테스트 미통과"
fi

# Gate 2: Shadow 결과 파일 존재 확인
SHADOW_FILE="$(git rev-parse --show-toplevel)/.omc/state/shadow-result-latest.json"
if [ ! -f "$SHADOW_FILE" ]; then
  ERRORS="${ERRORS}\n- Shadow 결과 파일 없음: .omc/state/shadow-result-latest.json"
else
  # Gate 3: 활성 전략 전부 trade >= 1 (dead strategy 방지)
  ZERO_TRADE=$(python3 -c "
import json, sys
try:
    with open('$SHADOW_FILE') as f:
        d = json.load(f)
    zero = [s['strategy_id'] for s in d.get('by_strategy', []) if s.get('trades', 0) == 0 and s.get('active', True)]
    if zero:
        print(','.join(zero))
except Exception as e:
    print(f'parse_error:{e}', file=sys.stderr)
" 2>/dev/null)
  if [ -n "$ZERO_TRADE" ]; then
    ERRORS="${ERRORS}\n- Dead Strategy (trade=0): $ZERO_TRADE — Type W(Wiring) 의심"
  fi

  # Gate 4: crash = 0
  CRASH_COUNT=$(python3 -c "
import json
with open('$SHADOW_FILE') as f:
    d = json.load(f)
print(d.get('crash_count', 0))
" 2>/dev/null)
  if [ "$CRASH_COUNT" != "0" ] && [ -n "$CRASH_COUNT" ]; then
    ERRORS="${ERRORS}\n- Shadow crash 발생: ${CRASH_COUNT}건"
  fi

  # Gate 5: PnL >= 0
  PNL_NEGATIVE=$(python3 -c "
import json
with open('$SHADOW_FILE') as f:
    d = json.load(f)
pnl = d.get('total_pnl', 0)
if pnl < 0:
    print(f'negative:{pnl}')
" 2>/dev/null)
  if [ -n "$PNL_NEGATIVE" ]; then
    ERRORS="${ERRORS}\n- Shadow PnL 음수: $PNL_NEGATIVE"
  fi

  # Gate 6: Profit Factor > 1.0 (절대 지표)
  PF_FAIL=$(python3 -c "
import json
with open('$SHADOW_FILE') as f:
    d = json.load(f)
pf = d.get('profit_factor', 0)
if pf is not None and pf <= 1.0 and d.get('total_trades', 0) > 10:
    print(f'low:{pf}')
" 2>/dev/null)
  if [ -n "$PF_FAIL" ]; then
    ERRORS="${ERRORS}\n- Profit Factor <= 1.0: $PF_FAIL (절대 지표 미달)"
  fi

  # Gate 7: MDD < 5%
  MDD_FAIL=$(python3 -c "
import json
with open('$SHADOW_FILE') as f:
    d = json.load(f)
mdd = d.get('max_drawdown_pct', 0)
if mdd is not None and mdd >= 5.0:
    print(f'high:{mdd}%')
" 2>/dev/null)
  if [ -n "$MDD_FAIL" ]; then
    ERRORS="${ERRORS}\n- Max Drawdown >= 5%: $MDD_FAIL (절대 지표 미달)"
  fi
fi

if [ -n "$ERRORS" ]; then
  echo -e "=== Assembly Gate BLOCKED ===$ERRORS" >&2
  echo -e "\n수정 후 Shadow 재실행 필요. Type W(Wiring) 의심 시 Stage A 재기획." >&2
  exit 2  # 차단! 에이전트에게 피드백 주입
fi

echo "Assembly Gate PASS" >&2
exit 0
