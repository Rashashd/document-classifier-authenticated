"""seed_casbin_policy

Revision ID: b1c2d3e4f5a6
Revises: 93ff30599200
Create Date: 2026-05-13 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = '93ff30599200'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POLICIES = [
    # p-type: role may act as itself
    ("p", "admin",    "admin"),
    ("p", "reviewer", "reviewer"),
    ("p", "auditor",  "auditor"),
    # g-type: admin inherits reviewer and auditor
    ("g", "admin", "reviewer"),
    ("g", "admin", "auditor"),
]


def upgrade() -> None:
    casbin_rule = sa.table(
        "casbin_rule",
        sa.column("ptype", sa.String),
        sa.column("v0",    sa.String),
        sa.column("v1",    sa.String),
    )
    op.bulk_insert(casbin_rule, [
        {"ptype": ptype, "v0": v0, "v1": v1}
        for ptype, v0, v1 in _POLICIES
    ])


def downgrade() -> None:
    op.execute(
        "DELETE FROM casbin_rule WHERE (ptype, v0, v1) IN %s"
        % str(tuple((p, v0, v1) for p, v0, v1 in _POLICIES))
    )
