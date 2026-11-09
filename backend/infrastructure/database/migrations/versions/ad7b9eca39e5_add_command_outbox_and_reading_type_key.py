"""add command outbox and reading type key

Revision ID: ad7b9eca39e5
Revises: 73d8da71ddf0
Create Date: 2026-10-15 12:46:19.791058
"""

from alembic import op
import sqlalchemy as sa


revision = 'ad7b9eca39e5'
down_revision = '73d8da71ddf0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("device_commands") as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "payload",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_device_commands_session_id_charging_sessions",
            "charging_sessions",
            ["session_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.drop_constraint("ck_device_commands_status", type_="check")
        batch_op.create_check_constraint(
            "ck_device_commands_status",
            "status IN ('pending', 'sent', 'confirmed', 'rejected', 'timed_out')",
        )

    with op.batch_alter_table("measurements") as batch_op:
        batch_op.drop_constraint("uq_measurements_message_resource", type_="unique")
        batch_op.create_unique_constraint(
            "uq_measurements_message_resource_reading",
            ["message_id", "resource", "reading_type"],
        )


def downgrade() -> None:
    with op.batch_alter_table("measurements") as batch_op:
        batch_op.drop_constraint(
            "uq_measurements_message_resource_reading", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_measurements_message_resource", ["message_id", "resource"]
        )

    with op.batch_alter_table("device_commands") as batch_op:
        batch_op.drop_constraint("ck_device_commands_status", type_="check")
        batch_op.create_check_constraint(
            "ck_device_commands_status",
            "status IN ('pending', 'confirmed', 'rejected', 'timed_out')",
        )
        batch_op.drop_constraint(
            "fk_device_commands_session_id_charging_sessions", type_="foreignkey"
        )
        batch_op.drop_column("expires_at")
        batch_op.drop_column("last_sent_at")
        batch_op.drop_column("attempts")
        batch_op.drop_column("payload")
        batch_op.drop_column("session_id")
