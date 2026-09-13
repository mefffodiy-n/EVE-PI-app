"""colony pin routes from ESI

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-09-13 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.add_column(sa.Column("routes", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.drop_column("routes")
