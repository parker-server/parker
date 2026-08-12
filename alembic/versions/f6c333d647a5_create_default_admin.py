"""legacy default admin seed retired

Revision ID: f6c333d647a5
Revises: 23bc0e2cee25
Create Date: 2025-12-13 11:50:24.008539

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'f6c333d647a5'
down_revision: Union[str, None] = '23bc0e2cee25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Parker now creates the first administrator through startup bootstrap
    # using INITIAL_ADMIN_PASSWORD. Existing databases that already ran the
    # legacy seed are intentionally left untouched.
    pass



def downgrade() -> None:
    pass
