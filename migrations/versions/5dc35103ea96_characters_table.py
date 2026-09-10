"""characters table

Revision ID: 5dc35103ea96
Revises: 
Create Date: 2026-09-11 00:08:26.626439

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5dc35103ea96'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Персонажи: dev-заглушки (source="dev") и будущие реальные из ESI
    # SSO (source="esi") в одной таблице — planner их не различает.
    op.create_table(
        "characters",
        sa.Column("character_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command_center_upgrades_level", sa.Integer(), nullable=False),
        sa.Column("interplanetary_consolidation_level", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("character_id"),
    )


def downgrade() -> None:
    op.drop_table("characters")
