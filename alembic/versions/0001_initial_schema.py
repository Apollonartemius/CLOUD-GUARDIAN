"""initial schema - full CloudGuardian table set

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-10

Mirrors exactly what the five platform services auto-create at startup:
incidents (+ the counterfactual/preduction columns), metric_readings,
ingestion_gaps, anomalies, forecasts, incident_reports.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("trigger_reason", sa.Text(), nullable=False),
        sa.Column("action_taken", sa.Text(), nullable=False),
        sa.Column("confidence_at_trigger", sa.Float(), nullable=False),
        sa.Column("incident_type", sa.Text(), nullable=False, server_default="reactive"),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("forecast_metric", sa.Text(), nullable=True),
        sa.Column("forecast_eta_minutes", sa.Float(), nullable=True),
        sa.Column("predicted_peak_value", sa.Float(), nullable=True),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("actual_peak_value", sa.Float(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("verification_json", JSONB(), nullable=True),
        sa.Column("action_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False, server_default="pending"),
    )
    op.create_index(
        "idx_incidents_service_time",
        "incidents",
        [sa.column("service_name"), sa.text("action_started_at DESC")],
    )

    op.create_table(
        "metric_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("memory_mb", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error_rate", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_metric_readings_service_time",
        "metric_readings",
        [sa.column("service_name"), sa.text("recorded_at DESC")],
    )

    op.create_table(
        "ingestion_gaps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("gap_seconds", sa.Float(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "anomalies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_anomalies_service_time",
        "anomalies",
        [sa.column("service_name"), sa.text("detected_at DESC")],
    )

    op.create_table(
        "forecasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("predicted_values", JSONB(), nullable=False),
        sa.Column("breach_risk", sa.Float(), nullable=True),
        sa.Column("breach_eta_minutes", sa.Float(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_forecasts_service_metric_time",
        "forecasts",
        [sa.column("service_name"), sa.column("metric_name"), sa.text("generated_at DESC")],
    )

    op.create_table(
        "incident_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("incident_reports")
    op.drop_index("idx_forecasts_service_metric_time", table_name="forecasts")
    op.drop_table("forecasts")
    op.drop_index("idx_anomalies_service_time", table_name="anomalies")
    op.drop_table("anomalies")
    op.drop_table("ingestion_gaps")
    op.drop_index("idx_metric_readings_service_time", table_name="metric_readings")
    op.drop_table("metric_readings")
    op.drop_index("idx_incidents_service_time", table_name="incidents")
    op.drop_table("incidents")