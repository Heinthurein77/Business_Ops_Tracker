"""license documents and low stock notify

Revision ID: 13f3abf4efd4
Revises: d76bb8780f1e
Create Date: 2026-10-20 10:33:34.577518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '13f3abf4efd4'
down_revision: Union[str, Sequence[str], None] = 'd76bb8780f1e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('license_documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('business_unit_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('document_type', sa.String(length=32), nullable=False),
    sa.Column('issue_date', sa.Date(), nullable=True),
    sa.Column('expiry_date', sa.Date(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('expiry_notified_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['business_unit_id'], ['business_units.id'], name=op.f('fk_license_documents_business_unit_id_business_units')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_license_documents_created_by_id_users')),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_license_documents_business_unit_id'), 'license_documents', ['business_unit_id'], unique=False)
    op.create_index(op.f('ix_license_documents_expiry_date'), 'license_documents', ['expiry_date'], unique=False)
    op.create_index(op.f('ix_license_documents_id'), 'license_documents', ['id'], unique=False)
    op.add_column('inventory_items', sa.Column('low_notified_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('inventory_items', 'low_notified_at')
    op.drop_index(op.f('ix_license_documents_id'), table_name='license_documents')
    op.drop_index(op.f('ix_license_documents_expiry_date'), table_name='license_documents')
    op.drop_index(op.f('ix_license_documents_business_unit_id'), table_name='license_documents')
    op.drop_table('license_documents')
