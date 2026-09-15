"""characters primary character id

Revision ID: 57fc4d4e8f63
Revises: b2c3d4e5f6a7
Create Date: 2026-09-16 01:36:00.393518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57fc4d4e8f63'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Постоянная привязка персонажа к группе «основной + альты»
    (docs/ROADMAP.md, Фаза 9, 16.09.2026) — независимая от cookie-сессии
    браузера (api/session.py), в отличие от account_id. NULL — персонаж
    не состоит ни в какой группе (текущее поведение для всех, пока
    группу не создали явно). Для персонажей внутри группы — character_id
    основного персонажа группы (сам основной ссылается на себя).
    """
    with op.batch_alter_table("characters") as batch_op:
        batch_op.add_column(sa.Column("primary_character_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("characters") as batch_op:
        batch_op.drop_column("primary_character_id")
