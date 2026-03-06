"""Tests for SQLAlchemy ORM models."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone


class TestPositionWALModel:
    """Test position_wal table schema."""

    def test_model_has_required_columns(self):
        """position_wal has all Amendment 1A required columns."""
        from src.infra.db.schema import PositionWAL
        from sqlalchemy import inspect

        mapper = inspect(PositionWAL)
        col_names = {c.key for c in mapper.columns}

        required = {
            "wal_id", "ts", "event_type", "strategy_id", "exchange_id",
            "symbol", "side", "quantity", "avg_price", "metadata", "checksum"
        }
        assert required.issubset(col_names), f"Missing columns: {required - col_names}"

    def test_event_type_valid_values(self):
        """event_type accepts OPEN, UPDATE, CLOSE, LOCK, UNLOCK."""
        from src.infra.db.schema import EventType

        assert EventType.OPEN.value == "OPEN"
        assert EventType.UPDATE.value == "UPDATE"
        assert EventType.CLOSE.value == "CLOSE"
        assert EventType.LOCK.value == "LOCK"
        assert EventType.UNLOCK.value == "UNLOCK"

    def test_side_valid_values(self):
        """side accepts LONG, SHORT, FLAT."""
        from src.infra.db.schema import PositionSide

        assert PositionSide.LONG.value == "LONG"
        assert PositionSide.SHORT.value == "SHORT"
        assert PositionSide.FLAT.value == "FLAT"

    def test_position_wal_tablename(self):
        """Table name is position_wal."""
        from src.infra.db.schema import PositionWAL

        assert PositionWAL.__tablename__ == "position_wal"

    def test_position_wal_indexes_defined(self):
        """Indexes on (strategy_id, ts) and (exchange_id, symbol) are defined."""
        from src.infra.db.schema import PositionWAL
        from sqlalchemy import inspect

        mapper = inspect(PositionWAL)
        table = mapper.mapped_table
        index_names = {idx.name for idx in table.indexes}

        assert "idx_wal_strategy_ts" in index_names
        assert "idx_wal_exchange_symbol" in index_names


class TestCapitalAllocationLockModel:
    """Test capital_allocation_lock table schema."""

    def test_model_has_required_columns(self):
        """capital_allocation_lock has all required columns."""
        from src.infra.db.schema import CapitalAllocationLock
        from sqlalchemy import inspect

        mapper = inspect(CapitalAllocationLock)
        col_names = {c.key for c in mapper.columns}

        required = {
            "lock_id", "ts", "strategy_id", "exchange_id",
            "amount", "currency", "status", "expires_at"
        }
        assert required.issubset(col_names)

    def test_lock_status_values(self):
        """status accepts ACQUIRED and RELEASED."""
        from src.infra.db.schema import LockStatus

        assert LockStatus.ACQUIRED.value == "ACQUIRED"
        assert LockStatus.RELEASED.value == "RELEASED"


class TestTradesModel:
    """Test trades table schema."""

    def test_model_has_required_columns(self):
        """trades has all required columns."""
        from src.infra.db.schema import Trade
        from sqlalchemy import inspect

        mapper = inspect(Trade)
        col_names = {c.key for c in mapper.columns}

        required = {
            "trade_id", "ts", "strategy_id", "exchange_id", "symbol",
            "side", "quantity", "price", "fee", "order_id"
        }
        assert required.issubset(col_names)


class TestOrdersModel:
    """Test orders table schema."""

    def test_model_has_required_columns(self):
        """orders has all required columns."""
        from src.infra.db.schema import Order
        from sqlalchemy import inspect

        mapper = inspect(Order)
        col_names = {c.key for c in mapper.columns}

        required = {
            "order_id", "ts", "strategy_id", "exchange_id", "symbol",
            "side", "type", "quantity", "price", "status", "filled_qty"
        }
        assert required.issubset(col_names)


class TestStrategyConfigModel:
    """Test strategy_config table schema."""

    def test_model_has_required_columns(self):
        """strategy_config has all required columns."""
        from src.infra.db.schema import StrategyConfig
        from sqlalchemy import inspect

        mapper = inspect(StrategyConfig)
        col_names = {c.key for c in mapper.columns}

        required = {
            "strategy_id", "type", "params", "is_active", "updated_at"
        }
        assert required.issubset(col_names)

    def test_strategy_id_is_primary_key(self):
        """strategy_id is the primary key."""
        from src.infra.db.schema import StrategyConfig
        from sqlalchemy import inspect

        mapper = inspect(StrategyConfig)
        pk_cols = {c.key for c in mapper.primary_key}
        assert "strategy_id" in pk_cols
