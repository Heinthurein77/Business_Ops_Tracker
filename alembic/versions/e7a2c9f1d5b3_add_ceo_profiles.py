"""add ceo_profiles table and users.is_profile_completed

Revision ID: e7a2c9f1d5b3
Revises: c3f1a9d7e2b4
Create Date: 2026-07-25 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a2c9f1d5b3'
down_revision: Union[str, Sequence[str], None] = 'c3f1a9d7e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('is_profile_completed', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # This is a brand-new mandatory requirement, so no existing CEO has
    # actually completed one yet — flip the flag back to false for that role
    # only, so the app-level default (server_default=true, meant for roles
    # this doesn't apply to) doesn't silently skip everyone already seeded.
    op.execute("UPDATE users SET is_profile_completed = false WHERE role = 'CEO'")

    op.create_table(
        'ceo_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('executive_title', sa.String(length=120), nullable=False, server_default='Chief Executive Officer'),
        sa.Column('phone_number', sa.String(length=30), nullable=False),
        sa.Column('date_of_birth', sa.Date(), nullable=False),
        sa.Column('address', sa.Text(), nullable=False),
        sa.Column('profile_pic_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_ceo_profile_user'),
    )
    op.create_index(op.f('ix_ceo_profiles_id'), 'ceo_profiles', ['id'], unique=False)
    op.create_index(op.f('ix_ceo_profiles_user_id'), 'ceo_profiles', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ceo_profiles_user_id'), table_name='ceo_profiles')
    op.drop_index(op.f('ix_ceo_profiles_id'), table_name='ceo_profiles')
    op.drop_table('ceo_profiles')
    op.drop_column('users', 'is_profile_completed')
