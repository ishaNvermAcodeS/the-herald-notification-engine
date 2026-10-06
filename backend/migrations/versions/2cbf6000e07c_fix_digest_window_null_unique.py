"""fix_digest_window_null_unique

Revision ID: 2cbf6000e07c
Revises: dc544dbaab6c
Create Date: 2026-10-06 04:03:08.286008

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2cbf6000e07c'
down_revision: Union[str, None] = 'dc544dbaab6c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        'ix_digest_window_open_unique',
        table_name='digest_windows',
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index(
        'ix_digest_window_open_unique',
        'digest_windows',
        ['subscriberId', 'workflowId', 'digestKey'],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_digest_window_open_unique',
        table_name='digest_windows',
        postgresql_where=sa.text("status = 'OPEN'"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        'ix_digest_window_open_unique',
        'digest_windows',
        ['subscriberId', 'workflowId', 'digestKey'],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
