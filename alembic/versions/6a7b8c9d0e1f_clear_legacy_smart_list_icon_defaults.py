"""Clear legacy smart list default icons

Revision ID: 6a7b8c9d0e1f
Revises: b7c9d0e1f2a3
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6a7b8c9d0e1f"
down_revision: Union[str, None] = "b7c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE smart_lists SET icon = NULL WHERE icon = :legacy_icon").bindparams(
            legacy_icon="\u26A1"
        )
    )


def downgrade() -> None:
    pass
