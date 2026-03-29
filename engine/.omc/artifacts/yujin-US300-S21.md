# US-300: 포트폴리오 통합 Shadow 1H — 구현 완료

## 변경 파일

### engine/src/modes/shadow.py
- `ShadowMode.__init__()`: `portfolio_risk: Any | None = None` 파라미터 추가 (line ~399)
  - `self._portfolio_risk = portfolio_risk` 저장 (US-300, 하위 호환)
- `_execute_shadow_trade()`: PnL 확정 후 `self._portfolio_risk.update_returns(sid, net_pnl_float)` 호출 (line ~1518)
  - None 가드 + 예외 캐치(non-fatal debug log)
- `get_snapshot()`: portfolio_metrics dict 생성 후 return에 **portfolio_metrics 스프레드 (line ~2214)
  - `portfolio_var_95`, `portfolio_volatility`, `portfolio_mdd_pct`, `portfolio_mdd_breach` 포함
  - PortfolioRiskManager가 None이거나 샘플 부족(MIN_SAMPLES=20)이면 빈 dict → 기존 키 유지

### engine/src/main.py
- 3개 ShadowMode() 호출 모두에 `portfolio_risk=self._portfolio_risk` 추가 (US-300)
  - line ~2344: `_run_shadow()` 첫 번째 호출 (self._shadow_mode)
  - line ~2449: `run_shadow_for_duration()` 로컬 shadow 변수
  - line ~2529: 세 번째 self._shadow_mode 호출

## WIRING AC 검증
1. **생성**: ShadowMode.__init__()에 `portfolio_risk` 파라미터 추가 ✅
2. **주입**: main.py 3곳 ShadowMode() 호출에 `portfolio_risk=self._portfolio_risk` 전달 ✅
3. **호출**: `_execute_shadow_trade()`에서 거래 완료 후 `update_returns()` 호출 + `get_snapshot()`에 메트릭 포함 ✅

## 테스트 결과
- `tests/unit/strategies/test_s18_portfolio.py` + `tests/unit/core/test_portfolio_risk.py`: 30 passed
- 전체 suite: **5205 passed, 0 failed, 12 skipped** (311s)

## PASS
