"""colonies table

Revision ID: 2aeafc4d5def
Revises: 0b072b31fba9
Create Date: 2026-09-11 00:59:11.772987

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2aeafc4d5def'
down_revision: Union[str, Sequence[str], None] = '0b072b31fba9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Снимок реальных колоний персонажа из ESI (sync_colony_status).
    # UtcDateTime в моделях = DateTime(timezone=True) на уровне DDL.
    op.create_table(
        "colonies",
        sa.Column("character_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("planet_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("planet_name", sa.String(length=128), nullable=False),
        sa.Column("planet_type", sa.String(length=32), nullable=False),
        sa.Column("upgrade_level", sa.Integer(), nullable=False),
        sa.Column("num_pins", sa.Integer(), nullable=False),
        sa.Column("nearest_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("character_id", "planet_id"),
    )


def downgrade() -> None:
    op.drop_table("colonies")
