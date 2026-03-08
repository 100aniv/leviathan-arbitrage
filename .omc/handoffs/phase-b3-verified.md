## Handoff: Phase B-3 (NewJeans/Development) → Phase B-4

### Decided
- TriangularScanner: Bellman-Ford with -log(rate) edge weights, depth-aware bottleneck volume
- RealDataSignalProducer: standalone class extracting 4 evaluators from shadow.py
- Shadow wiring: __init__ creates RealDataSignalProducer with DI, _evaluate_multi_strategies delegates
- Funding rate loop: delegates to RealDataSignalProducer.on_funding_rates_updated()
- 6 inline _evaluate_* methods deleted from shadow.py (-384 lines)
- `statistics` import removed (no longer needed after stat_arb extraction)

### Rejected
- Keeping inline methods alongside RealDataSignalProducer (dead code, confusing)
- Brute-force cycle enumeration (O(n!) vs Bellman-Ford O(V*E))
- Modifying strategy classes (consumers unchanged, only signal producers changed)

### Risks
- RealDataSignalProducer.on_orderbook_update() calls all evaluators on every update — perf OK for current symbol count but may need throttling at >500 symbols
- stat_arb/latency_arb remain in RealDataSignalProducer but are NOT called (shadow disables them) — these strategies are NOT_READY

### Files
- engine/src/core/triangular_scanner.py (NEW, 285 lines — Bellman-Ford scanner)
- engine/src/core/real_signal_producer.py (NEW, 334 lines — 4 evaluators)
- engine/src/modes/shadow.py (MODIFIED, 1022 lines — -384 lines, DI + delegation)
- engine/tests/unit/test_triangular_scanner.py (NEW, 18 tests)
- engine/tests/unit/test_real_signal_producer.py (NEW, 10 tests)
- engine/tests/integration/test_multi_signal_integration.py (NEW, 17 tests)
- SSOT.md (UPDATED, GAP 7+3+2 RESOLVED)

### Metrics
- Tests: 3,180 passed, 0 failed (+45 new)
- Coverage: 90%
- GAP 7: RESOLVED (TriangularScanner replaces brute-force triangle_finder)
- GAP 3: RESOLVED (RealDataSignalProducer replaces inline shadow.py evaluators)
- GAP 2: RESOLVED (Shadow mode wiring delegates to extracted producer)

### Next (Phase B-4): Shadow Integration (GAP 1) — US-023~026
- US-023: ShadowMode에 StrategyManager 주입
- US-024: _on_orderbook StrategyManager 라우팅
- US-025: 전략별 메트릭 추적 (per-strategy PnL)
- US-026: Shadow 전략 통합 테스트
