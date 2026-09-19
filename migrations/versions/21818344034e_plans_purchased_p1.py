"""plans purchased_p1

Revision ID: 21818344034e
Revises: 57fc4d4e8f63
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '21818344034e'
down_revision: Union[str, Sequence[str], None] = '57fc4d4e8f63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(sa.Column("purchased_p1", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("purchased_p1")
