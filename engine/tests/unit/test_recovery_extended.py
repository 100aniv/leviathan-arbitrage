"""Extended coverage tests for src/infra/db/recovery.py."""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# RecoveryManager.__init__
# ---------------------------------------------------------------------------

class TestRecoveryManagerInit:
    def test_init_defaults_all_none(self):
        from src.infra.db.recovery import RecoveryManager
        manager = RecoveryManager()
        assert manager._db is None
        assert manager._redis is None
        assert manager._exchange_clients == {}

    def test_init_with_all_params(self):
        from src.infra.db.recovery import RecoveryManager
        mock_db = MagicMock()
        mock_redis = MagicMock()
        mock_clients = {"binance": MagicMock()}
        manager = RecoveryManager(
            db_pool=mock_db,
            redis_client=mock_redis,
            exchange_clients=mock_clients,
        )
        assert manager._db is mock_db
        assert manager._redis is mock_redis
        assert manager._exchange_clients is mock_clients

    def test_init_exchange_clients_defaults_to_empty_dict(self):
        from src.infra.db.recovery import RecoveryManager
        manager = RecoveryManager(exchange_clients=None)
        assert manager._exchange_clients == {}


# ---------------------------------------------------------------------------
# RecoveryManager.on_redis_unavailable
# ---------------------------------------------------------------------------

class TestOnRedisUnavailable:
    def test_calls_halt_method(self):
        from src.infra.db.recovery import RecoveryManager
        manager = RecoveryManager.__new__(RecoveryManager)
        manager._halt = MagicMock()
        manager.on_redis_unavailable()
        manager._halt.assert_called_once()

    def test_actual_halt_sets_kill_switch(self):
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import is_halted, clear_halt
        clear_halt()
        manager = RecoveryManager()
        manager.on_redis_unavailable()
        assert is_halted() is True
        clear_halt()


# ---------------------------------------------------------------------------
# RecoveryManager._write_wal_to_redis
# ---------------------------------------------------------------------------

class TestWriteWalToRedis:
    @pytest.mark.asyncio
    async def test_skips_close_event_entries(self):
        from src.infra.db.recovery import RecoveryManager

        mock_pipeline = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        manager = RecoveryManager(redis_client=mock_redis)

        wal_entries = [{
            "strategy_id": "s1",
            "exchange_id": "binance",
            "symbol": "BTC/USDT",
            "side": "LONG",
            "quantity": Decimal("0.1"),
            "avg_price": Decimal("50000"),
            "event_type": "CLOSE",
            "wal_id": 1,
        }]

        result = await manager._write_wal_to_redis(wal_entries)
        assert result is True
        mock_pipeline.hset.assert_not_called()
        mock_pipeline.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_writes_open_entry_to_redis(self):
        from src.infra.db.recovery import RecoveryManager

        mock_pipeline = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        manager = RecoveryManager(redis_client=mock_redis)

        wal_entries = [{
            "strategy_id": "strat1",
            "exchange_id": "binance",
            "symbol": "BTC/USDT",
            "side": "LONG",
            "quantity": Decimal("0.1"),
            "avg_price": Decimal("50000"),
            "event_type": "OPEN",
            "wal_id": 42,
        }]

        result = await manager._write_wal_to_redis(wal_entries)
        assert result is True
        mock_pipeline.hset.assert_called_once()
        # Verify the Redis key format
        call_args = mock_pipeline.hset.call_args
        key = call_args[0][0]
        assert "strat1" in key
        assert "binance" in key
        assert "BTC/USDT" in key

    @pytest.mark.asyncio
    async def test_writes_multiple_open_entries(self):
        from src.infra.db.recovery import RecoveryManager

        mock_pipeline = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        manager = RecoveryManager(redis_client=mock_redis)

        wal_entries = [
            {"strategy_id": "s1", "exchange_id": "binance", "symbol": "BTC/USDT",
             "side": "LONG", "quantity": Decimal("0.1"), "avg_price": Decimal("50000"),
             "event_type": "OPEN", "wal_id": 1},
            {"strategy_id": "s1", "exchange_id": "okx", "symbol": "ETH/USDT",
             "side": "SHORT", "quantity": Decimal("1.0"), "avg_price": Decimal("2000"),
             "event_type": "OPEN", "wal_id": 2},
        ]

        await manager._write_wal_to_redis(wal_entries)
        assert mock_pipeline.hset.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_entries_calls_execute(self):
        from src.infra.db.recovery import RecoveryManager

        mock_pipeline = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        manager = RecoveryManager(redis_client=mock_redis)
        result = await manager._write_wal_to_redis([])
        assert result is True
        mock_pipeline.execute.assert_called_once()


# ---------------------------------------------------------------------------
# RecoveryManager._reconcile_with_exchange
# ---------------------------------------------------------------------------

class TestReconcileWithExchange:
    @pytest.mark.asyncio
    async def test_empty_entries_returns_true(self):
        from src.infra.db.recovery import RecoveryManager
        manager = RecoveryManager()
        assert await manager._reconcile_with_exchange([]) is True

    @pytest.mark.asyncio
    async def test_none_entries_returns_true(self):
        from src.infra.db.recovery import RecoveryManager
        manager = RecoveryManager()
        assert await manager._reconcile_with_exchange(None) is True

    @pytest.mark.asyncio
    async def test_missing_client_logs_and_skips(self):
        from src.infra.db.recovery import RecoveryManager
        manager = RecoveryManager(exchange_clients={})  # no clients

        entries = [{"exchange_id": "binance", "symbol": "BTC/USDT", "quantity": Decimal("0.1")}]
        result = await manager._reconcile_with_exchange(entries)
        # Missing client → skip → return True
        assert result is True

    @pytest.mark.asyncio
    async def test_fetch_position_failure_returns_false(self):
        from src.infra.db.recovery import RecoveryManager

        mock_client = AsyncMock()
        mock_client.fetch_position.side_effect = Exception("API error")
        manager = RecoveryManager(exchange_clients={"binance": mock_client})

        entries = [{"exchange_id": "binance", "symbol": "BTC/USDT", "quantity": Decimal("0.1")}]
        result = await manager._reconcile_with_exchange(entries)
        assert result is False

    @pytest.mark.asyncio
    async def test_position_mismatch_returns_false(self):
        from src.infra.db.recovery import RecoveryManager

        mock_client = AsyncMock()
        mock_client.fetch_position.return_value = {"quantity": "0.5"}  # WAL says 0.1

        manager = RecoveryManager(exchange_clients={"binance": mock_client})
        entries = [{"exchange_id": "binance", "symbol": "BTC/USDT", "quantity": Decimal("0.1")}]
        result = await manager._reconcile_with_exchange(entries)
        assert result is False

    @pytest.mark.asyncio
    async def test_position_match_within_tolerance_returns_true(self):
        from src.infra.db.recovery import RecoveryManager

        mock_client = AsyncMock()
        # 0.10001 vs 0.1: diff = 0.00001, tolerance = 0.1 * 0.0001 = 0.00001 → within tolerance
        mock_client.fetch_position.return_value = {"quantity": "0.10001"}

        manager = RecoveryManager(exchange_clients={"binance": mock_client})
        entries = [{"exchange_id": "binance", "symbol": "BTC/USDT", "quantity": Decimal("0.1")}]
        result = await manager._reconcile_with_exchange(entries)
        assert result is True

    @pytest.mark.asyncio
    async def test_exact_position_match_returns_true(self):
        from src.infra.db.recovery import RecoveryManager

        mock_client = AsyncMock()
        mock_client.fetch_position.return_value = {"quantity": "0.1"}

        manager = RecoveryManager(exchange_clients={"binance": mock_client})
        entries = [{"exchange_id": "binance", "symbol": "BTC/USDT", "quantity": Decimal("0.1")}]
        result = await manager._reconcile_with_exchange(entries)
        assert result is True


# ---------------------------------------------------------------------------
# RecoveryManager._get_latest_wal_entries
# ---------------------------------------------------------------------------

class TestGetLatestWalEntries:
    @pytest.mark.asyncio
    async def test_queries_db_and_returns_dicts(self):
        from src.infra.db.recovery import RecoveryManager

        mock_rows = [
            {"strategy_id": "s1", "exchange_id": "binance", "symbol": "BTC/USDT",
             "side": "LONG", "quantity": 0.1, "avg_price": 50000.0,
             "event_type": "OPEN", "wal_id": 1}
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = mock_rows

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.pool.acquire.return_value = mock_pool_ctx

        manager = RecoveryManager(db_pool=mock_pool)
        entries = await manager._get_latest_wal_entries()

        assert len(entries) == 1
        assert entries[0]["symbol"] == "BTC/USDT"
        assert entries[0]["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_no_rows(self):
        from src.infra.db.recovery import RecoveryManager

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.pool.acquire.return_value = mock_pool_ctx

        manager = RecoveryManager(db_pool=mock_pool)
        entries = await manager._get_latest_wal_entries()
        assert entries == []


# ---------------------------------------------------------------------------
# RecoveryManager.recover (full sequence)
# ---------------------------------------------------------------------------

class TestRecoverFullSequence:
    @pytest.mark.asyncio
    async def test_recover_returns_true_on_success(self):
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import clear_halt, halt_local

        halt_local()
        manager = RecoveryManager.__new__(RecoveryManager)
        manager._get_latest_wal_entries = AsyncMock(return_value=[])
        manager._write_wal_to_redis = AsyncMock(return_value=True)
        manager._reconcile_with_exchange = AsyncMock(return_value=True)
        manager._clear_halt = MagicMock(side_effect=clear_halt)

        result = await manager.recover()
        assert result is True
        manager._clear_halt.assert_called_once()
        clear_halt()

    @pytest.mark.asyncio
    async def test_recover_returns_false_on_reconcile_failure(self):
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import clear_halt, halt_local

        halt_local()
        manager = RecoveryManager.__new__(RecoveryManager)
        manager._get_latest_wal_entries = AsyncMock(return_value=[])
        manager._write_wal_to_redis = AsyncMock(return_value=True)
        manager._reconcile_with_exchange = AsyncMock(return_value=False)
        manager._clear_halt = MagicMock()

        result = await manager.recover()
        assert result is False
        manager._clear_halt.assert_not_called()
        clear_halt()

    @pytest.mark.asyncio
    async def test_recover_calls_all_steps_in_order(self):
        from src.infra.db.recovery import RecoveryManager
        from src.risk.kill_switch import clear_halt, halt_local

        halt_local()
        call_order = []

        async def track_wal():
            call_order.append("wal")
            return []

        async def track_redis(entries):
            call_order.append("redis")
            return True

        async def track_reconcile(entries=None):
            call_order.append("reconcile")
            return True

        manager = RecoveryManager.__new__(RecoveryManager)
        manager._get_latest_wal_entries = track_wal
        manager._write_wal_to_redis = track_redis
        manager._reconcile_with_exchange = track_reconcile
        manager._clear_halt = MagicMock(side_effect=clear_halt)

        await manager.recover()
        assert call_order == ["wal", "redis", "reconcile"]
        clear_halt()
