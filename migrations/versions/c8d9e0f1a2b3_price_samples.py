"""price samples history

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-09-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("station", sa.String(length=32), nullable=False),
        sa.Column("product_type_id", sa.Integer(), nullable=False),
        sa.Column("buy_max", sa.Float(), nullable=True),
        sa.Column("sell_min", sa.Float(), nullable=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("price_samples") as batch_op:
        batch_op.create_index(
            "ix_price_samples_lookup", ["station", "product_type_id", "sampled_at"]
        )
        batch_op.create_index("ix_price_samples_sampled_at", ["sampled_at"])


def downgrade() -> None:
    op.drop_table("price_samples")
