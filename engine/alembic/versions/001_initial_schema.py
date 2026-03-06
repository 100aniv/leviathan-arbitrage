"""Initial schema: position_wal, capital_allocation_lock, trades, orders, strategy_config.

Revision ID: 001
Revises:
Create Date: 2026-03-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── position_wal ──────────────────────────────────────────────────────────
    op.create_table(
        "position_wal",
        sa.Column("wal_id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("exchange_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("avg_price", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("checksum", sa.Text(), nullable=False),
    )
    op.create_index("idx_wal_strategy_ts", "position_wal", ["strategy_id", sa.text("ts DESC")])
    op.create_index("idx_wal_exchange_symbol", "position_wal", ["exchange_id", "symbol"])

    # ── capital_allocation_lock ───────────────────────────────────────────────
    op.create_table(
        "capital_allocation_lock",
        sa.Column("lock_id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("exchange_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── trades ────────────────────────────────────────────────────────────────
    op.create_table(
        "trades",
        sa.Column("trade_id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("exchange_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("price", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("fee", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=False),
    )

    # ── orders ────────────────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("order_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("exchange_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("price", sa.Numeric(precision=28, scale=10), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "filled_qty",
            sa.Numeric(precision=28, scale=10),
            nullable=False,
            server_default="0",
        ),
    )

    # ── strategy_config ───────────────────────────────────────────────────────
    op.create_table(
        "strategy_config",
        sa.Column("strategy_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("strategy_config")
    op.drop_table("orders")
    op.drop_table("trades")
    op.drop_table("capital_allocation_lock")
    op.drop_index("idx_wal_exchange_symbol", table_name="position_wal")
    op.drop_index("idx_wal_strategy_ts", table_name="position_wal")
    op.drop_table("position_wal")
