"""LEVIATHAN Runtime Modules — Phase 4 main.py 모듈화 (2026-04-26).

main.py 4203 LOC를 책임 단위로 분리:
- ml_loops:           HMM/XGBoost/Regime/AdaptiveThreshold 백그라운드 훈련 루프
- bootstrap:          (TBD) Config + DB + Telegram + Rust + Tuner 초기화
- exchange_init:      (TBD) Paper/Sandbox/Live/Native 어댑터 wiring
- pipeline_init:      (TBD) SignalGenerator + StrategyManager + DEX
- risk_execution:     (TBD) RiskGuardian + AtomicExecutor + on_execution_result
- mode_loops:         (TBD) paper/live/backtest/shadow_validation 루프 디스패치
- background_loops:   (TBD) health/heartbeat/reconcile/PM drain 등

각 함수는 첫 인자로 ``engine: "Engine"`` 인스턴스를 받음. ``Engine`` 클래스 메서드는
이 모듈로 위임만 함 (thin wrapper) — 외부 호출 시그니처 보존.
"""
