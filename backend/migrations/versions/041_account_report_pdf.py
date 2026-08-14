"""Remember where an account's analysis PDF was written.

The file is uploaded to blob storage, so without the URL the client would have
to regenerate the PDF to download it again — paying to rebuild a document that
already exists.

Revision ID: 041
Revises: 040
"""
from alembic import op
import sqlalchemy as sa

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_accounts", sa.Column("analysis_pdf_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_accounts", "analysis_pdf_url")
