"""colony structures

Revision ID: 946bc27481dc
Revises: aa02b8427932
Create Date: 2026-09-11 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '946bc27481dc'
down_revision: Union[str, Sequence[str], None] = 'aa02b8427932'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default='[]' — на случай, если в colonies уже есть строки
    # (следующая синхронизация всё равно их перезапишет реальным составом).
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.add_column(sa.Column("structures", sa.JSON(),
                                      nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.drop_column("structures")
