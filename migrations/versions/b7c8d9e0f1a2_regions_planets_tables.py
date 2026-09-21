"""regions and planets tables

Revision ID: b7c8d9e0f1a2
Revises: a3f7d2e91c48
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'a3f7d2e91c48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "planets",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("constellation", sa.String(length=64), nullable=False),
        sa.Column("system", sa.String(length=64), nullable=False),
        sa.Column("planet_number", sa.Integer(), nullable=False),
        sa.Column("planet_type", sa.String(length=32), nullable=False),
        sa.Column("radius_km", sa.Float(), nullable=True),
        sa.Column("poco_tax_rate", sa.Float(), nullable=True),
        sa.Column("poco_owner", sa.String(length=64), nullable=True),
        sa.Column("r0_densities", sa.JSON(), nullable=True),
        sa.Column("p2_direct_densities", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
    )


def downgrade() -> None:
    op.drop_table("planets")
    op.drop_table("regions")
