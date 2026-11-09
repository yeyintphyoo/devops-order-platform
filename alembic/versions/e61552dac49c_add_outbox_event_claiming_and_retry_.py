"""add outbox event claiming and retry fields

Revision ID: e61552dac49c
Revises: 451316fade36
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e61552dac49c"
down_revision: Union[str, Sequence[str], None] = "451316fade36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add event claiming and retry scheduling fields."""

    op.add_column(
        "outbox_events",
        sa.Column("locked_by", sa.String(length=100), nullable=True),
    )

    op.add_column(
        "outbox_events",
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "outbox_events",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_outbox_events_claiming",
        "outbox_events",
        ["published_at", "next_attempt_at", "locked_until", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove event claiming and retry scheduling fields."""

    op.drop_index(
        "ix_outbox_events_claiming",
        table_name="outbox_events",
    )

    op.drop_column("outbox_events", "next_attempt_at")
    op.drop_column("outbox_events", "locked_until")
    op.drop_column("outbox_events", "locked_by")