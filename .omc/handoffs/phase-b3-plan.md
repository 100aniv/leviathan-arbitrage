## Handoff: Phase A (aespa/Planning) → Phase B (NewJeans/Development) — B-3

### Decided
- TriangularScanner: Bellman-Ford with -log(rate) edge weights, depth-aware profit calc
- RealDataSignalProducer: new class extracting 4 inline _evaluate_* from shadow.py
- Shadow wiring: delegate _evaluate_multi_strategies to RealDataSignalProducer
- Paper mode unchanged (PaperSignalSimulator path untouched)
- TriangleCycle dataclass for scanner results
- Dependency: US-019 → US-020 → US-021 → US-022

### Rejected
- Brute-force cycle enumeration (O(n!) vs Bellman-Ford O(V*E))
- Floyd-Warshall (overhead for all-pairs when we only need USDT-connected cycles)
- Extending MultiStrategySignalProducer (new class keeps separation of concerns)
- Modifying strategy classes (consumers unchanged, only producers change)

### Risks
- Shadow.py extraction: 6 methods removed (~400 lines) — exact logic copy required
- Bellman-Ford perf on dense graphs — mitigated by currency count pruning
- Korean exchange stale data → false triangular signals (excluded by min_exchanges)

### Files
- .omc/plans/US-019-022_PLAN.md (full plan with interface contracts)

### Task Split for NewJeans
- **Hanni (executor)**: US-019 (TriangularScanner) + US-020 (RealDataSignalProducer)
- **Haerin (test-engineer)**: US-021 (Shadow wiring) + US-022 (integration tests)
