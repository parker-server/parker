"""migrate random login covers

Revision ID: f3a9c1d2e4b6
Revises: bd6f7a4c9e20
Create Date: 2026-08-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f3a9c1d2e4b6"
down_revision: Union[str, None] = "bd6f7a4c9e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE system_settings
        SET value = 'cycling_static_covers'
        WHERE key = 'ui.login_background_style'
          AND value = 'random_covers'
        """
    )


def downgrade() -> None:
    # Do not reintroduce the public-login library-cover mode on downgrade.
    pass
