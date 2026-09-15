"""extraction_samples.pin_id widened to BigInteger

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-15 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_samples") as batch_op:
        batch_op.alter_column("pin_id", type_=sa.BigInteger())


def downgrade() -> None:
    with op.batch_alter_table("extraction_samples") as batch_op:
        batch_op.alter_column("pin_id", type_=sa.Integer())
