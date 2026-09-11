"""extraction samples history

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-11 20:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extraction_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("planet_id", sa.Integer(), nullable=False),
        sa.Column("pin_id", sa.Integer(), nullable=False),
        sa.Column("product_type_id", sa.Integer(), nullable=True),
        sa.Column("qty_per_cycle", sa.Integer(), nullable=True),
        sa.Column("cycle_seconds", sa.Integer(), nullable=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("extraction_samples") as batch_op:
        batch_op.create_index("ix_extraction_samples_character_id", ["character_id"])
        batch_op.create_index("ix_extraction_samples_planet_id", ["planet_id"])
        batch_op.create_index("ix_extraction_samples_sampled_at", ["sampled_at"])


def downgrade() -> None:
    op.drop_table("extraction_samples")
