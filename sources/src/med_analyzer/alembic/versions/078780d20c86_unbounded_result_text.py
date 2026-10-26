"""Make result text columns unbounded.

Synevo reference blocks already reach several hundred characters, close to
the old 512-character limit, and an overlong value would fail the upload.

Revision ID: 078780d20c86
Revises: f9c1ae8098a3
Create Date: 2026-10-22 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '078780d20c86'
down_revision: str | None = 'f9c1ae8098a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Change ``results.ref_raw`` and ``results.value_text`` to ``TEXT``."""
    op.alter_column(
        'results',
        'ref_raw',
        type_=sa.Text(),
        existing_type=sa.String(length=512),
        existing_nullable=True,
    )
    op.alter_column(
        'results',
        'value_text',
        type_=sa.Text(),
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Restore the bounded types; fails rather than truncating longer rows."""
    op.alter_column(
        'results',
        'value_text',
        type_=sa.String(length=255),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        'results',
        'ref_raw',
        type_=sa.String(length=512),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
