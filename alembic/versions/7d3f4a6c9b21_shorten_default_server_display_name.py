"""Shorten default server display name

Revision ID: 7d3f4a6c9b21
Revises: 4b8d4f7f6c21
Create Date: 2026-07-15 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7d3f4a6c9b21"
down_revision: Union[str, None] = "4b8d4f7f6c21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE system_settings
        SET value = 'Parker'
        WHERE key = 'general.app_name'
          AND value = 'Parker Comic Server'
        """
    )


def downgrade() -> None:
    # This data migration is intentionally not reversed because we cannot
    # safely distinguish migrated defaults from intentional user-selected
    # values of "Parker" during downgrade.
    pass
