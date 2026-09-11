"""colony cpu/pg absolute numbers

Revision ID: d4e5f6a7b8c9
Revises: c1a2f3e4b5d6
Create Date: 2026-09-11 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c1a2f3e4b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.add_column(sa.Column("cpu_used", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("cpu_capacity", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("pg_used", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("pg_capacity", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.drop_column("pg_capacity")
        batch_op.drop_column("pg_used")
        batch_op.drop_column("cpu_capacity")
        batch_op.drop_column("cpu_used")
