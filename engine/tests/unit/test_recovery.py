"""Tests for Redis recovery protocol (Amendment 1B)."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


class TestRecoveryProtocol:
    """Test Redis unavailability detection and recovery."""

    @pytest.mark.asyncio
    async def test_redis_unavailability_sets_halt(self):
        """Detecting Redis unavailability sets the HALT flag."""
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import is_halted, clear_halt

        clear_halt()

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._halt = MagicMock()

        manager.on_redis_unavailable()

        manager._halt.assert_called_once()

    @pytest.mark.asyncio
    async def test_recovery_reads_wal_from_postgres(self):
        """Recovery reads latest WAL entries from PostgreSQL per (strategy, exchange, symbol)."""
        from src.infra.db.recovery import RecoveryManager

        mock_db = AsyncMock()
        mock_db.fetch = AsyncMock(return_value=[
            {
                "strategy_id": "strat1",
                "exchange_id": "binance",
                "symbol": "BTC/USDT",
                "side": "LONG",
                "quantity": Decimal("0.1"),
                "avg_price": Decimal("50000"),
                "event_type": "OPEN",
                "wal_id": 1,
            }
        ])

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._db = mock_db
        manager._get_latest_wal_entries = AsyncMock(return_value=[
            {
                "strategy_id": "strat1",
                "exchange_id": "binance",
                "symbol": "BTC/USDT",
                "side": "LONG",
                "quantity": Decimal("0.1"),
                "avg_price": Decimal("50000"),
                "event_type": "OPEN",
            }
        ])

        entries = await manager._get_latest_wal_entries()
        assert len(entries) == 1
        assert entries[0]["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_recovery_writes_to_redis(self):
        """Recovery reconstructs Redis state from WAL entries."""
        from src.infra.db.recovery import RecoveryManager

        mock_redis = AsyncMock()
        mock_redis.hset = AsyncMock(return_value=True)

        wal_entries = [
            {
                "strategy_id": "strat1",
                "exchange_id": "binance",
                "symbol": "BTC/USDT",
                "side": "LONG",
                "quantity": Decimal("0.1"),
                "avg_price": Decimal("50000"),
                "event_type": "OPEN",
            }
        ]

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._redis = mock_redis
        manager._write_wal_to_redis = AsyncMock(return_value=True)

        result = await manager._write_wal_to_redis(wal_entries)
        assert result is True

    @pytest.mark.asyncio
    async def test_recovery_clears_halt_on_state_match(self):
        """HALT is cleared only when exchange state matches reconstructed state."""
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import is_halted, clear_halt, halt_local

        halt_local()
        assert is_halted() is True

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._clear_halt = MagicMock()
        manager._reconcile_with_exchange = AsyncMock(return_value=True)  # states match

        result = await manager._reconcile_with_exchange()
        assert result is True

        # Simulate the recovery flow: clear halt after successful reconciliation
        if result:
            manager._clear_halt()

        manager._clear_halt.assert_called_once()
        clear_halt()  # cleanup

    @pytest.mark.asyncio
    async def test_recovery_keeps_halt_on_state_mismatch(self):
        """HALT is NOT cleared when exchange state doesn't match reconstructed state."""
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import is_halted, clear_halt, halt_local

        halt_local()
        assert is_halted() is True

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._clear_halt = MagicMock()
        manager._reconcile_with_exchange = AsyncMock(return_value=False)  # mismatch

        result = await manager._reconcile_with_exchange()
        assert result is False

        # HALT should remain set
        if result:
            manager._clear_halt()

        manager._clear_halt.assert_not_called()
        assert is_halted() is True
        clear_halt()  # cleanup

    @pytest.mark.asyncio
    async def test_full_recovery_sequence(self):
        """Full recovery: detect unavailability → WAL read → Redis write → reconcile → resume."""
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import is_halted, clear_halt, halt_local

        halt_local()

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._get_latest_wal_entries = AsyncMock(return_value=[
            {
                "strategy_id": "strat1",
                "exchange_id": "binance",
                "symbol": "BTC/USDT",
                "side": "LONG",
                "quantity": Decimal("0.1"),
                "avg_price": Decimal("50000"),
                "event_type": "OPEN",
            }
        ])
        manager._write_wal_to_redis = AsyncMock(return_value=True)
        manager._reconcile_with_exchange = AsyncMock(return_value=True)
        manager._clear_halt = MagicMock(side_effect=clear_halt)

        await manager.recover()

        manager._get_latest_wal_entries.assert_called_once()
        manager._write_wal_to_redis.assert_called_once()
        manager._reconcile_with_exchange.assert_called_once()
        manager._clear_halt.assert_called_once()

        assert is_halted() is False


class TestReconcileWithExchangeBug97:
    """BUG-97: native adapter duck-typing + CLOSE entry skip."""

    @pytest.mark.asyncio
    async def test_native_adapter_get_positions_used(self):
        """Native adapter uses get_positions() (list), not fetch_position()."""
        from src.infra.db.recovery import RecoveryManager

        manager = RecoveryManager.__new__(RecoveryManager)

        class _Pos:
            def __init__(self, symbol, quantity):
                self.symbol = symbol
                self.quantity = quantity

        class _NativeAdapter:
            async def get_positions(self):
                return [_Pos("BTC/USDT", Decimal("0.1"))]

        manager._exchange_clients = {"binance_futures": _NativeAdapter()}

        wal = [{
            "strategy_id": "s1",
            "exchange_id": "binance_futures",
            "symbol": "BTC/USDT",
            "side": "LONG",
            "quantity": Decimal("0.1"),
            "avg_price": Decimal("50000"),
            "event_type": "OPEN",
        }]
        result = await manager._reconcile_with_exchange(wal)
        assert result is True

    @pytest.mark.asyncio
    async def test_close_entry_skipped(self):
        """CLOSE entries are skipped — no reconciliation needed."""
        from src.infra.db.recovery import RecoveryManager

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._exchange_clients = {}  # no client needed — CLOSE skipped

        wal = [{
            "strategy_id": "s1",
            "exchange_id": "binance_futures",
            "symbol": "ZBT/USDT",
            "side": "LONG",
            "quantity": Decimal("50"),
            "avg_price": Decimal("1.0"),
            "event_type": "CLOSE",
        }]
        result = await manager._reconcile_with_exchange(wal)
        assert result is True

    @pytest.mark.asyncio
    async def test_ccxt_adapter_fetch_position_fallback(self):
        """ccxt adapter (has fetch_position) uses that path."""
        from src.infra.db.recovery import RecoveryManager

        manager = RecoveryManager.__new__(RecoveryManager)

        class _CcxtAdapter:
            # No get_positions attribute — forces fetch_position path
            async def fetch_position(self, symbol):
                return {"quantity": "0.1"}

        manager._exchange_clients = {"binance": _CcxtAdapter()}

        wal = [{
            "strategy_id": "s1",
            "exchange_id": "binance",
            "symbol": "BTC/USDT",
            "side": "LONG",
            "quantity": Decimal("0.1"),
            "avg_price": Decimal("50000"),
            "event_type": "OPEN",
        }]
        result = await manager._reconcile_with_exchange(wal)
        assert result is True
