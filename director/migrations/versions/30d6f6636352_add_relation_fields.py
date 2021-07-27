"""Add relation fields

Revision ID: 30d6f6636352
Revises: 2ac615d6850b
Create Date: 2021-07-10 18:11:16.312444

"""

from alembic import op
from director.extensions import db
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = '30d6f6636352'
down_revision = '2ac615d6850b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'workflows',
        db.Column('parent_id',
                  UUID,
                  db.ForeignKey('workflows.id'),
                  index=True
                  )
    )


def downgrade():
    pass
