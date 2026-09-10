"""colony system and index

Revision ID: aa02b8427932
Revises: 2aeafc4d5def
Create Date: 2026-09-11 01:09:49.033074

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa02b8427932'
down_revision: Union[str, Sequence[str], None] = '2aeafc4d5def'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default — на случай, если в colonies уже есть строки
    # (следующая синхронизация всё равно их перезапишет).
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.add_column(sa.Column("system_name", sa.String(length=64),
                                      nullable=False, server_default=""))
        batch_op.add_column(sa.Column("planet_index", sa.Integer(),
                                      nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("colonies") as batch_op:
        batch_op.drop_column("planet_index")
        batch_op.drop_column("system_name")
