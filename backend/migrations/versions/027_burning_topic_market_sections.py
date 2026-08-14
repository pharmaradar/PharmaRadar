"""Market-research sections on burning-topic and congress reports

Revision ID: 027
Revises: 026
Create Date: 2026-08-12

The client asked every Burning Topic to show the same market-research structure
as Topic Explorer: executive summary, so what, what is being said, voice
distribution, volume of mentions, key sub-topics.

The report already stored summary / key_findings / so_what / important_posts /
main_authors, so this adds only what was missing. Existing reports keep working:
every column is nullable and the UI renders a section only when it has content,
so a report generated before this migration simply shows the sections it has.

`period_days` moves onto the report row as `window_days` so a stored report
records the window it was actually generated over, rather than being reread
against whatever the topic's setting happens to be later.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    # Prose sections.
    ("what_is_said", sa.Text()),
    ("voices_note", sa.Text()),
    ("volume_note", sa.Text()),
    # Structured, computed from rows rather than written by the model.
    ("subtopics", sa.Text()),      # JSON list[str]
    ("voice_rows", sa.Text()),     # JSON list[{bucket,label,mentions,percent}]
    ("volume", sa.Text()),         # JSON dict
)


def _has_table(table: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def _has_column(table: str, column: str) -> bool:
    insp = Inspector.from_engine(op.get_bind())
    try:
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade():
    table = "burning_topic_reports"
    if not _has_table(table):
        return

    for name, coltype in _NEW_COLUMNS:
        if not _has_column(table, name):
            op.add_column(table, sa.Column(name, coltype, nullable=True))

    for name, coltype, default in (
        ("item_count", sa.Integer(), "0"),
        ("voice_exact_share", sa.Integer(), "0"),
        ("window_days", sa.Integer(), "30"),
    ):
        if not _has_column(table, name):
            op.add_column(table, sa.Column(name, coltype, nullable=False,
                                           server_default=default))


def downgrade():
    table = "burning_topic_reports"
    if not _has_table(table):
        return
    for name in ("window_days", "voice_exact_share", "item_count"):
        if _has_column(table, name):
            op.drop_column(table, name)
    for name, _ in reversed(_NEW_COLUMNS):
        if _has_column(table, name):
            op.drop_column(table, name)
