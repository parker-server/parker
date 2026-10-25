"""Strip raw_xml from comic metadata_json

Revision ID: c5f1a9d3e7b2
Revises: b8e2f4a6c1d3
Create Date: 2026-10-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c5f1a9d3e7b2"
down_revision: Union[str, None] = "b8e2f4a6c1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # raw_xml was a full copy of ComicInfo.xml that nothing read back.
    # Invalid JSON rows are left alone so rehydrate can still report them.
    # The file shrinks on the next container start (BackupService.compact_database_if_needed).
    op.execute(
        sa.text(
            "UPDATE comics "
            "SET metadata_json = json_remove(metadata_json, '$.raw_xml') "
            "WHERE metadata_json LIKE '%\"raw_xml\"%' AND json_valid(metadata_json)"
        )
    )


def downgrade() -> None:
    # Nothing to restore: raw_xml was never read, and the XML is still in each archive.
    pass
