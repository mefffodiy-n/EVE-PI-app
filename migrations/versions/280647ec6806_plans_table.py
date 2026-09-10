"""plans table

Revision ID: 280647ec6806
Revises: 5dc35103ea96
Create Date: 2026-09-11 00:20:40.413538

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '280647ec6806'
down_revision: Union[str, Sequence[str], None] = '5dc35103ea96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Сохранённые планы — замена json-файлов в data/plans/.
    # request/rows/warnings/assumptions — JSON (в Postgres станет JSONB).
    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("plans")
