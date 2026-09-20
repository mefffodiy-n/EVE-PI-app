"""credentials tokens to Text

Revision ID: cf63cab41bfc
Revises: 21818344034e
Create Date: 2026-09-20 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf63cab41bfc'
down_revision: Union[str, Sequence[str], None] = '21818344034e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # access_token/refresh_token были VARCHAR без длины — легально на
    # Postgres/SQLite, но MySQL/MariaDB требует явную длину для VARCHAR
    # (найдено 20.09.2026 при проверке схемы на MariaDB: `alembic
    # upgrade head` падал уже на создании таблицы credentials).
    with op.batch_alter_table("credentials") as batch_op:
        batch_op.alter_column("access_token", type_=sa.Text(), existing_nullable=False)
        batch_op.alter_column("refresh_token", type_=sa.Text(), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("credentials") as batch_op:
        batch_op.alter_column("access_token", type_=sa.String(), existing_nullable=False)
        batch_op.alter_column("refresh_token", type_=sa.String(), existing_nullable=False)
