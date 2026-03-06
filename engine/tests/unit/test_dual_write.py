"""
Tests for dual-write protocol (Amendment 1B).

CRITICAL: PG write MUST happen BEFORE Redis write.
- PG timeout: 5ms. Failure = REJECT trade.
- Redis timeout: 2ms. Failure = HALT engine.
- Checksum: SHA256(strategy_id + exchange_id + symbol + side + quantity + avg_price)
"""

import pytest
import asyncio
import hashlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call


class TestDualWriteSequence:
    """Test the mandatory PG → Redis write sequence."""

    @pytest.mark.asyncio
    async def test_pg_written_before_redis(self):
        """PG write MUST precede Redis write — sequence is non-negotiable."""
        from src.infra.db.dual_write import DualWriter

        call_order = []

        async def mock_pg_write(*args, **kwargs):
            call_order.append("postgres")

        async def mock_redis_write(*args, **kwargs):
            call_order.append("redis")

        writer = DualWriter.__new__(DualWriter)
        writer._write_to_postgres = mock_pg_write
        writer._write_to_redis = mock_redis_write

        await writer.write_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("0.1"),
            avg_price=Decimal("50000"),
            event_type="OPEN",
        )

        assert call_order == ["postgres", "redis"], (
            f"Expected PG before Redis, got: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_pg_failure_rejects_trade_does_not_proceed_to_redis(self):
        """If PG write fails, trade is rejected and Redis write is NEVER called."""
        from src.infra.db.dual_write import DualWriter, TradeRejectedError

        redis_called = False

        async def mock_pg_write(*args, **kwargs):
            raise Exception("PG timeout")

        async def mock_redis_write(*args, **kwargs):
            nonlocal redis_called
            redis_called = True

        writer = DualWriter.__new__(DualWriter)
        writer._write_to_postgres = mock_pg_write
        writer._write_to_redis = mock_redis_write

        with pytest.raises(TradeRejectedError):
            await writer.write_position(
                strategy_id="strat1",
                exchange_id="binance",
                symbol="BTC/USDT",
                side="LONG",
                quantity=Decimal("0.1"),
                avg_price=Decimal("50000"),
                event_type="OPEN",
            )

        assert redis_called is False, "Redis should NOT be called when PG fails"

    @pytest.mark.asyncio
    async def test_redis_failure_sets_halt_flag(self):
        """If Redis write fails, HALT flag is set."""
        from src.infra.db.dual_write import DualWriter
        from src.risk.kill_switch import is_halted, clear_halt

        # Ensure halt is clear before test
        clear_halt()

        async def mock_pg_write(*args, **kwargs):
            pass

        async def mock_redis_write(*args, **kwargs):
            raise Exception("Redis timeout")

        writer = DualWriter.__new__(DualWriter)
        writer._write_to_postgres = mock_pg_write
        writer._write_to_redis = mock_redis_write

        with pytest.raises(Exception):
            await writer.write_position(
                strategy_id="strat1",
                exchange_id="binance",
                symbol="BTC/USDT",
                side="LONG",
                quantity=Decimal("0.1"),
                avg_price=Decimal("50000"),
                event_type="OPEN",
            )

        assert is_halted() is True, "HALT flag must be set when Redis write fails"
        clear_halt()  # cleanup

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_raise_trade_rejected(self):
        """Redis failure raises a different error than PG failure (HALT, not REJECT)."""
        from src.infra.db.dual_write import DualWriter, TradeRejectedError, EngineHaltError
        from src.risk.kill_switch import clear_halt

        clear_halt()

        async def mock_pg_write(*args, **kwargs):
            pass

        async def mock_redis_write(*args, **kwargs):
            raise Exception("Redis timeout")

        writer = DualWriter.__new__(DualWriter)
        writer._write_to_postgres = mock_pg_write
        writer._write_to_redis = mock_redis_write

        with pytest.raises(EngineHaltError):
            await writer.write_position(
                strategy_id="strat1",
                exchange_id="binance",
                symbol="BTC/USDT",
                side="LONG",
                quantity=Decimal("0.1"),
                avg_price=Decimal("50000"),
                event_type="OPEN",
            )

        clear_halt()

    @pytest.mark.asyncio
    async def test_success_returns_wal_id(self):
        """Successful dual-write returns the WAL entry ID."""
        from src.infra.db.dual_write import DualWriter

        async def mock_pg_write(*args, **kwargs):
            return 42  # wal_id

        async def mock_redis_write(*args, **kwargs):
            return True

        writer = DualWriter.__new__(DualWriter)
        writer._write_to_postgres = mock_pg_write
        writer._write_to_redis = mock_redis_write

        result = await writer.write_position(
            strategy_id="strat1",
            exchange_id="binance",
            symbol="BTC/USDT",
            side="LONG",
            quantity=Decimal("0.1"),
            avg_price=Decimal("50000"),
            event_type="OPEN",
        )

        assert result == 42


class TestChecksum:
    """Test SHA256 checksum computation."""

    def test_checksum_is_sha256(self):
        """Checksum is SHA256(strategy_id + exchange_id + symbol + side + quantity + avg_price)."""
        from src.infra.db.dual_write import compute_checksum

        strategy_id = "strat1"
        exchange_id = "binance"
        symbol = "BTC/USDT"
        side = "LONG"
        quantity = Decimal("0.1")
        avg_price = Decimal("50000")

        expected = hashlib.sha256(
            f"{strategy_id}{exchange_id}{symbol}{side}{quantity}{avg_price}".encode()
        ).hexdigest()

        result = compute_checksum(strategy_id, exchange_id, symbol, side, quantity, avg_price)
        assert result == expected

    def test_checksum_is_deterministic(self):
        """Same inputs always produce same checksum."""
        from src.infra.db.dual_write import compute_checksum

        args = ("s1", "ex1", "ETH/USDT", "SHORT", Decimal("1.5"), Decimal("3000"))
        assert compute_checksum(*args) == compute_checksum(*args)

    def test_checksum_differs_for_different_inputs(self):
        """Different inputs produce different checksums."""
        from src.infra.db.dual_write import compute_checksum

        c1 = compute_checksum("s1", "ex1", "BTC/USDT", "LONG", Decimal("1"), Decimal("50000"))
        c2 = compute_checksum("s1", "ex1", "BTC/USDT", "LONG", Decimal("2"), Decimal("50000"))
        assert c1 != c2


class TestPGTimeout:
    """Test PG 5ms timeout enforcement."""

    @pytest.mark.asyncio
    async def test_pg_timeout_triggers_trade_rejection(self):
        """PG write exceeding timeout raises TradeRejectedError."""
        from src.infra.db.dual_write import DualWriter, TradeRejectedError

        async def slow_pg_write(*args, **kwargs):
            await asyncio.sleep(10)  # simulate slow PG

        async def mock_redis_write(*args, **kwargs):
            pass

        writer = DualWriter.__new__(DualWriter)
        writer._write_to_postgres = slow_pg_write
        writer._write_to_redis = mock_redis_write
        writer._pg_timeout = 0.001  # 1ms for test speed

        with pytest.raises(TradeRejectedError):
            await writer.write_position(
                strategy_id="strat1",
                exchange_id="binance",
                symbol="BTC/USDT",
                side="LONG",
                quantity=Decimal("0.1"),
                avg_price=Decimal("50000"),
                event_type="OPEN",
            )

    @pytest.mark.asyncio
    async def test_redis_timeout_triggers_halt(self):
        """Redis write exceeding timeout raises EngineHaltError and sets HALT."""
        from src.infra.db.dual_write import DualWriter, EngineHaltError
        from src.risk.kill_switch import is_halted, clear_halt

        clear_halt()

        async def mock_pg_write(*args, **kwargs):
            return 1

        async def slow_redis_write(*args, **kwargs):
            await asyncio.sleep(10)  # simulate slow Redis

        writer = DualWriter.__new__(DualWriter)
        writer._write_to_postgres = mock_pg_write
        writer._write_to_redis = slow_redis_write
        writer._redis_timeout = 0.001  # 1ms for test speed

        with pytest.raises(EngineHaltError):
            await writer.write_position(
                strategy_id="strat1",
                exchange_id="binance",
                symbol="BTC/USDT",
                side="LONG",
                quantity=Decimal("0.1"),
                avg_price=Decimal("50000"),
                event_type="OPEN",
            )

        assert is_halted() is True
        clear_halt()
