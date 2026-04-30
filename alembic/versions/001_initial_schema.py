"""Initial schema

Revision ID: 001
Create Date: 2024-01-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "signals",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("timestamp", sa.DateTime, nullable=False, index=True),
        sa.Column("symbol", sa.String(50), nullable=False, index=True),
        sa.Column("direction", sa.String(10), nullable=False, index=True),
        sa.Column("trade_type", sa.String(20), nullable=False),
        sa.Column("confidence", sa.String(5), nullable=False, index=True),
        sa.Column("gate_score", sa.Float),
        sa.Column("strike", sa.Float),
        sa.Column("expiry", sa.String(20)),
        sa.Column("option_type", sa.String(5)),
        sa.Column("premium", sa.Float),
        sa.Column("iv", sa.Float),
        sa.Column("entry_zone", sa.String(200)),
        sa.Column("stop_loss", sa.String(200)),
        sa.Column("target_1", sa.String(200)),
        sa.Column("target_2", sa.String(200)),
        sa.Column("rr_ratio", sa.String(20)),
        sa.Column("position_sizing", sa.String(200)),
        sa.Column("regime_type", sa.String(50)),
        sa.Column("vix_level", sa.Float),
        sa.Column("rsi_value", sa.Float),
        sa.Column("pcr_value", sa.Float),
        sa.Column("payload", JSON),
    )
    op.create_index("ix_signals_ts_symbol", "signals", ["timestamp", "symbol"])

    op.create_table(
        "regime_snapshots",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("timestamp", sa.DateTime, nullable=False, index=True),
        sa.Column("regime_type", sa.String(50), nullable=False),
        sa.Column("nifty_bias", sa.String(20)),
        sa.Column("banknifty_bias", sa.String(20)),
        sa.Column("overall_bias", sa.String(20)),
        sa.Column("vix_level", sa.Float),
        sa.Column("vix_signal", sa.String(30)),
        sa.Column("breadth_score", sa.Float),
        sa.Column("call_buying_env", sa.String(5)),
        sa.Column("put_buying_env", sa.String(5)),
        sa.Column("reason", sa.String(500)),
        sa.Column("raw", JSON),
    )

    op.create_table(
        "alert_log",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("signal_id", sa.String, nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime, nullable=False),
        sa.Column("channel", sa.String(30)),
        sa.Column("delivered", sa.Boolean, default=False),
        sa.Column("error", sa.Text, nullable=True),
    )

    # TimescaleDB hypertable for regime_snapshots (time-series)
    op.execute("SELECT create_hypertable('regime_snapshots', 'timestamp', if_not_exists => TRUE);")


def downgrade():
    op.drop_table("alert_log")
    op.drop_table("regime_snapshots")
    op.drop_index("ix_signals_ts_symbol", table_name="signals")
    op.drop_table("signals")
