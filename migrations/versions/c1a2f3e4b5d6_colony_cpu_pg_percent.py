"""colony cpu/pg percent

Revision ID: c1a2f3e4b5d6
Revises: bb723c4fd745
Create Date: 2026-09-11 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2f3e4b5d6'
down_revision: Union[str, Sequence[str], None] = 'bb723c4fd745'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.add_column(sa.Column("cpu_percent", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("pg_percent", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.drop_column("pg_percent")
        batch_op.drop_column("cpu_percent")
