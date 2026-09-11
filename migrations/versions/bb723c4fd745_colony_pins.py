"""colony pins

Revision ID: bb723c4fd745
Revises: 946bc27481dc
Create Date: 2026-09-11 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb723c4fd745'
down_revision: Union[str, Sequence[str], None] = '946bc27481dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default='[]' — на случай, если в colonies уже есть строки
    # (следующая синхронизация всё равно их перезапишет реальным составом).
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.add_column(sa.Column("pins", sa.JSON(),
                                      nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.drop_column("pins")
