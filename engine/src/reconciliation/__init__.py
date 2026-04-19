"""Reconciliation boundary — ground-truth PnL ledger (Path-B Day-1/2/3).

This package is the SINGLE SOURCE OF TRUTH for operator-facing PnL. It
resolves the divergence between the engine's internal ``self._stats.total_pnl``
and the exchange-reported realised/commission/funding income.

Public surface (populated incrementally as Day-1/2/3 modules land):

- Day-1: :class:`ExchangePnLSnapshot`, :class:`PnLReconciler`,
  :class:`PnLLedger` — exchange income snapshots + rolling comparator.
- Day-2: pre-trade validator (lives under :mod:`src.execution`).
- Day-3: :class:`DailyReport`, :class:`DailyReconciliationReport`,
  :func:`start_daily_report_scheduler` — operator-facing daily morning
  report delivered at UTC 00:05 with variance decomposition + CSV log.

All imports are guarded so each day can land independently.

Design principles:

- ``src/modes/live.py`` and ``src/main.py`` are *frozen* — injection wiring
  is a single attribute line in each.
- Re-uses the already-present low-level fetchers in
  :mod:`src.infra.exchange.exchange_income_fetcher` (no duplication).
- Timescale availability is probed at runtime; fallbacks are deterministic
  so unit tests can pin a known code path.
"""
from __future__ import annotations

__all__: list[str] = []

# Day-1 optional re-exports
try:  # pragma: no cover — optional until Day-1 lands
    from src.reconciliation.exchange_pnl_snapshot import (  # noqa: F401
        ExchangePnLSnapshot,
    )
    from src.reconciliation.pnl_ledger import (  # noqa: F401
        PnLLedger,
        PnLStatus,
    )
    from src.reconciliation.pnl_reconciler import PnLReconciler  # noqa: F401

    __all__ += ["ExchangePnLSnapshot", "PnLLedger", "PnLReconciler", "PnLStatus"]
except ImportError:
    pass

# Day-3 optional re-exports — Daily reconciliation report + scheduler.
try:  # pragma: no cover — optional
    from src.reconciliation.daily_report import (  # noqa: F401
        DailyReconciliationReport,
        DailyReport,
        VarianceDecomp,
    )

    __all__ += ["DailyReconciliationReport", "DailyReport", "VarianceDecomp"]
except ImportError:
    pass

try:  # pragma: no cover — optional
    from src.reconciliation.daily_report_scheduler import (  # noqa: F401
        start_daily_report_scheduler,
        stop_daily_report_scheduler,
    )

    __all__ += ["start_daily_report_scheduler", "stop_daily_report_scheduler"]
except ImportError:
    pass
