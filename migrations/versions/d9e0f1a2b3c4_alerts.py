"""alert subscriptions and sent log

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-09-29 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, Sequence[str], None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("webhook_url_enc", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(length=2), nullable=False),
        sa.Column("expiry_lead_hours", sa.Integer(), nullable=False),
        sa.Column("on_expiry", sa.Boolean(), nullable=False),
        sa.Column("on_deficit", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "alert_sent",
        sa.Column("subscription_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("alert_sent")
    op.drop_table("alert_subscriptions")
