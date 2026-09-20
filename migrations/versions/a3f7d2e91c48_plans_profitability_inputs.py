"""plans profitability_inputs

Revision ID: a3f7d2e91c48
Revises: cf63cab41bfc
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f7d2e91c48'
down_revision: Union[str, Sequence[str], None] = 'cf63cab41bfc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(sa.Column("profitability_inputs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("profitability_inputs")
