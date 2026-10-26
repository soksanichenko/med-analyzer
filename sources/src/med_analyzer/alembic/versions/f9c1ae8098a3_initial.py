"""Initial schema.

Revision ID: f9c1ae8098a3
Revises:
Create Date: 2026-10-22 00:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f9c1ae8098a3'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all tables."""
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=64), nullable=False),
        sa.Column('sex', sa.String(length=1), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('last_patient_name', sa.String(length=255), nullable=True),
        sa.CheckConstraint("sex IN ('M', 'F')", name='ck_users_sex'),
        sa.PrimaryKeyConstraint('id', name='pk_users'),
        sa.UniqueConstraint('discord_user_id', name='uq_users_discord_user_id'),
    )
    op.create_table(
        'analytes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_analytes'),
        sa.UniqueConstraint('code', name='uq_analytes_code'),
    )
    op.create_table(
        'analyte_aliases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('analyte_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ['analyte_id'],
            ['analytes.id'],
            name='fk_analyte_aliases_analyte_id_analytes',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_analyte_aliases'),
        sa.UniqueConstraint('alias', name='uq_analyte_aliases_alias'),
    )
    op.create_index('ix_analyte_aliases_analyte_id', 'analyte_aliases', ['analyte_id'])
    op.create_table(
        'analysis_sets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('lab', sa.String(length=32), nullable=False),
        sa.Column('collected_on', sa.Date(), nullable=False),
        sa.Column('order_number', sa.String(length=64), nullable=True),
        sa.Column('patient_name', sa.String(length=255), nullable=True),
        sa.Column(
            'warnings',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lab IN ('lab_service', 'synevo')", name='ck_analysis_sets_lab'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name='fk_analysis_sets_user_id_users'
        ),
        sa.PrimaryKeyConstraint('id', name='pk_analysis_sets'),
    )
    op.create_index('ix_analysis_sets_user_id', 'analysis_sets', ['user_id'])
    op.create_index(
        'uq_analysis_sets_user_lab_order',
        'analysis_sets',
        ['user_id', 'lab', 'order_number'],
        unique=True,
        postgresql_where=sa.text('order_number IS NOT NULL'),
    )
    op.create_table(
        'set_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('set_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column(
            'uploaded_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['set_id'],
            ['analysis_sets.id'],
            name='fk_set_files_set_id_analysis_sets',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name='fk_set_files_user_id_users'
        ),
        sa.PrimaryKeyConstraint('id', name='pk_set_files'),
        sa.UniqueConstraint('user_id', 'sha256', name='uq_set_files_user_id_sha256'),
    )
    op.create_index('ix_set_files_set_id', 'set_files', ['set_id'])
    op.create_table(
        'results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('set_id', sa.Integer(), nullable=False),
        sa.Column('analyte_id', sa.Integer(), nullable=False),
        sa.Column('raw_name', sa.String(length=255), nullable=False),
        sa.Column('value', sa.Numeric(), nullable=True),
        sa.Column('value_text', sa.String(length=255), nullable=True),
        sa.Column('unit', sa.String(length=64), nullable=True),
        sa.Column('flag', sa.String(length=16), nullable=True),
        sa.Column('ref_raw', sa.String(length=512), nullable=True),
        sa.Column(
            'ref_groups',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column('ref_low', sa.Numeric(), nullable=True),
        sa.Column('ref_high', sa.Numeric(), nullable=True),
        sa.CheckConstraint(
            "flag IN ('high', 'low', 'attention')", name='ck_results_flag'
        ),
        sa.ForeignKeyConstraint(
            ['analyte_id'], ['analytes.id'], name='fk_results_analyte_id_analytes'
        ),
        sa.ForeignKeyConstraint(
            ['set_id'],
            ['analysis_sets.id'],
            name='fk_results_set_id_analysis_sets',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_results'),
    )
    op.create_index('ix_results_analyte_id', 'results', ['analyte_id'])
    op.create_index('ix_results_set_id', 'results', ['set_id'])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('results')
    op.drop_table('set_files')
    op.drop_table('analysis_sets')
    op.drop_table('analyte_aliases')
    op.drop_table('analytes')
    op.drop_table('users')
