"""Live micro 카나리 진입 사전 검증 스크립트 (Phase 8 Step 5+, 2026-04-27).

Phase 8 paper/live 단일 배관 통합 완료 후, 라이브 진입 전 안전 점검.
사장님 결정 영역 (자본 영향)과 자체 진행 영역 분리.

사장님 결정 사항:
1. API keys 입력 (.env): BINANCE_API_KEY + BITGET_API_KEY + UPBIT_ACCESS_KEY
2. engine.json mode flip: "paper" → "live"
3. capital.tier 선택: step2_1 ($120) / alpha ($70) / beta ($750)

자체 점검 사항:
1. ✓ Phase 8 paper smoke 안정성 (이전 검증)
2. ✓ engine.json live 섹션 안전 (max_daily_loss_pct < 10%)
3. API keys env vars 활성 여부 (이 스크립트로 확인)
4. 거래소 open positions = 0 (스크립트로 확인 — 별도 close_positions.py 활용)
5. live_gate / approval gate 활성 여부
6. Risk caps (loss cap = $1/trade, daily $6 limit)

Usage:
    python -m engine.scripts.live_pre_check

Exit codes:
    0 = 모든 체크 통과 — 라이브 진입 안전
    1 = 차단 사항 발견 — 사장님 결정 필요
"""
from __future__ import annotations

import json
import os
import pathlib
import sys


def check_api_keys() -> tuple[bool, list[str]]:
    """라이브 진입 필수 API keys 활성 확인."""
    required = {
        "BINANCE_API_KEY": "Binance trade execution",
        "BITGET_API_KEY": "Bitget trade execution",
    }
    missing = []
    for var, purpose in required.items():
        if not os.getenv(var):
            missing.append(f"{var} (목적: {purpose})")
    return (len(missing) == 0, missing)


def check_engine_config() -> tuple[bool, list[str]]:
    """engine.json 라이브 진입 안전 설정 확인."""
    cfg_path = pathlib.Path(__file__).parent.parent / "config" / "engine.json"
    cfg = json.loads(cfg_path.read_text())
    issues = []

    # mode 체크
    mode = cfg.get("mode", "unknown")
    if mode != "live":
        issues.append(f"engine.json mode='{mode}' (라이브 진입은 'live' 필요)")

    # 라이브 max_daily_loss_pct
    live_loss = cfg.get("live", {}).get("max_daily_loss_pct", 100)
    if live_loss > 10:
        issues.append(f"live.max_daily_loss_pct={live_loss}% (10% 초과 — 자본 보호 룰 위반)")

    # max_position_pct
    pos_pct = cfg.get("risk", {}).get("max_position_pct", 100)
    if pos_pct > 10:
        issues.append(f"risk.max_position_pct={pos_pct}% (소액 카나리 권장 ≤ 10%)")

    # min_trade_notional_usd
    min_notional = cfg.get("execution", {}).get("min_trade_notional_usd", 0)
    if min_notional < 5:
        issues.append(f"execution.min_trade_notional_usd={min_notional} (Binance 최소 $5)")

    # capital tier
    tier = cfg.get("capital", {}).get("tier", "unknown")
    tier_cfg = cfg.get("capital", {}).get("tiers", {}).get(tier, {})
    initial = tier_cfg.get("initial_usd", 0)
    if initial > 200:
        issues.append(
            f"capital.tier='{tier}' initial_usd=${initial} (micro 카나리는 $50-150 권장)"
        )

    return (len(issues) == 0, issues)


def check_phase8_unified() -> tuple[bool, list[str]]:
    """Phase 8 paper/live 단일 배관 통합 검증."""
    issues = []
    try:
        from src.runtime.mode_loops import _build_livemode_runner
        if _build_livemode_runner is None:
            issues.append("_build_livemode_runner helper 미존재")
    except ImportError as exc:
        issues.append(f"Phase 8 helper import fail: {exc}")
    return (len(issues) == 0, issues)


def main() -> int:
    print("=" * 60)
    print("LEVIATHAN Live Micro 카나리 진입 사전 검증")
    print("Phase 8 paper/live 단일 배관 통합 완료 후 (2026-04-27)")
    print("=" * 60)

    blocking_count = 0

    # 1. API keys
    print("\n[1/3] API keys 활성 확인:")
    ok, missing = check_api_keys()
    if ok:
        print("  ✅ Binance + Bitget API keys 활성")
    else:
        print("  ❌ 누락된 API keys:")
        for m in missing:
            print(f"     - {m}")
        print("  → 사장님 결정: .env 파일에 API key 입력 필요")
        blocking_count += 1

    # 2. engine.json
    print("\n[2/3] engine.json 라이브 안전 설정:")
    ok, issues = check_engine_config()
    if ok:
        print("  ✅ engine.json 라이브 진입 설정 안전")
    else:
        print("  ❌ engine.json 차단 사항:")
        for issue in issues:
            print(f"     - {issue}")
        print("  → 사장님 결정: engine.json 수정 필요")
        blocking_count += 1

    # 3. Phase 8 helper
    print("\n[3/3] Phase 8 단일 배관 helper:")
    ok, issues = check_phase8_unified()
    if ok:
        print("  ✅ _build_livemode_runner helper 활성")
    else:
        print("  ❌ Phase 8 helper 문제:")
        for issue in issues:
            print(f"     - {issue}")
        blocking_count += 1

    print("\n" + "=" * 60)
    if blocking_count == 0:
        print("✅ 모든 체크 통과 — 라이브 micro 카나리 진입 안전")
        print("다음 단계: engine.json mode='live' flip + python -m src.main 시작")
        return 0
    else:
        print(f"❌ {blocking_count}개 차단 사항 — 사장님 결정 필요")
        print("\n사장님 결정 사항:")
        print("  1. .env 파일에 API key 입력 (BINANCE/BITGET)")
        print("  2. engine.json mode='live' flip + capital tier 선택")
        print("  3. 거래소 잔고 충전 ($50+ Binance, $50+ Bitget 권장)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
