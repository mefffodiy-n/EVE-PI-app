"""credentials table

Revision ID: 0b072b31fba9
Revises: 280647ec6806
Create Date: 2026-09-11 00:34:54.295918

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b072b31fba9'
down_revision: Union[str, Sequence[str], None] = '280647ec6806'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Токены ESI одного персонажа. access_token/refresh_token лежат
    # зашифрованными (infra.crypto, Fernet) — в открытом виде их в БД нет.
    op.create_table(
        "credentials",
        sa.Column("character_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("character_id"),
    )


def downgrade() -> None:
    op.drop_table("credentials")
