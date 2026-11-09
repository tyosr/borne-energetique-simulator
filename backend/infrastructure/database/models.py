"""Schéma PostgreSQL du backend, exprimé avec SQLAlchemy 2."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.infrastructure.database.base import Base


class Consumer(Base):
    __tablename__ = "consumers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    tags: Mapped[list[AccessTag]] = relationship(back_populates="consumer")
    reservations: Mapped[list[Reservation]] = relationship(back_populates="consumer")


class AccessTag(Base):
    __tablename__ = "access_tags"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_access_tags_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    consumer_id: Mapped[UUID] = mapped_column(
        ForeignKey("consumers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    consumer: Mapped[Consumer] = relationship(back_populates="tags")


class ChargingPoint(Base):
    __tablename__ = "charging_points"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    plug_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reservations: Mapped[list[Reservation]] = relationship(back_populates="charging_point")
    sessions: Mapped[list[ChargingSession]] = relationship(back_populates="charging_point")


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (
        CheckConstraint(
            "resource IN ('ELECTRICITE', 'EAU', 'GAZ')", name="ck_tariffs_resource"
        ),
        UniqueConstraint("name", "resource", "version", name="uq_tariffs_version"),
        CheckConstraint("version > 0", name="ck_tariffs_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    resource: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    tiers: Mapped[list[TariffTier]] = relationship(
        back_populates="tariff", cascade="all, delete-orphan", order_by="TariffTier.lower_bound"
    )
    reservations: Mapped[list[ReservationTariff]] = relationship(back_populates="tariff")


class TariffTier(Base):
    __tablename__ = "tariff_tiers"
    __table_args__ = (
        CheckConstraint("lower_bound >= 0", name="ck_tariff_tiers_lower_nonnegative"),
        CheckConstraint(
            "upper_bound IS NULL OR upper_bound > lower_bound",
            name="ck_tariff_tiers_bounds",
        ),
        CheckConstraint("unit_price >= 0", name="ck_tariff_tiers_price_nonnegative"),
        UniqueConstraint("tariff_id", "lower_bound", name="uq_tariff_tiers_lower_bound"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tariff_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariffs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lower_bound: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    upper_bound: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    tariff: Mapped[Tariff] = relationship(back_populates="tiers")


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_reservations_valid_period"),
        CheckConstraint(
            "status IN ('pending', 'active', 'completed', 'cancelled', 'rejected')",
            name="ck_reservations_status",
        ),
        Index("ix_reservations_point_period", "charging_point_id", "starts_at", "ends_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    consumer_id: Mapped[UUID] = mapped_column(
        ForeignKey("consumers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    charging_point_id: Mapped[str] = mapped_column(
        ForeignKey("charging_points.id", ondelete="RESTRICT"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    consumer: Mapped[Consumer] = relationship(back_populates="reservations")
    charging_point: Mapped[ChargingPoint] = relationship(back_populates="reservations")
    tariffs: Mapped[list[ReservationTariff]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[ChargingSession]] = relationship(back_populates="reservation")


class ReservationTariff(Base):
    __tablename__ = "reservation_tariffs"
    __table_args__ = (
        CheckConstraint(
            "resource IN ('ELECTRICITE', 'EAU', 'GAZ')",
            name="ck_reservation_tariffs_resource",
        ),
        UniqueConstraint("reservation_id", "resource", name="uq_reservation_resource_tariff"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    reservation_id: Mapped[UUID] = mapped_column(
        ForeignKey("reservations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource: Mapped[str] = mapped_column(String(24), nullable=False)
    tariff_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariffs.id", ondelete="RESTRICT"), nullable=False
    )

    reservation: Mapped[Reservation] = relationship(back_populates="tariffs")
    tariff: Mapped[Tariff] = relationship(back_populates="reservations")


class ChargingSession(Base):
    __tablename__ = "charging_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_activation', 'active', 'inactive', 'completed', 'failed')",
            name="ck_charging_sessions_status",
        ),
        Index(
            "uq_charging_sessions_one_active_per_point",
            "charging_point_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    reservation_id: Mapped[UUID] = mapped_column(
        ForeignKey("reservations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    charging_point_id: Mapped[str] = mapped_column(
        ForeignKey("charging_points.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending_activation"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    reservation: Mapped[Reservation] = relationship(back_populates="sessions")
    charging_point: Mapped[ChargingPoint] = relationship(back_populates="sessions")
    measurements: Mapped[list[Measurement]] = relationship(back_populates="session")


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    message_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        CheckConstraint(
            "resource IN ('ELECTRICITE', 'EAU', 'GAZ')", name="ck_measurements_resource"
        ),
        CheckConstraint(
            "reading_type IN ('delta', 'index')", name="ck_measurements_reading_type"
        ),
        CheckConstraint("value >= 0", name="ck_measurements_value_nonnegative"),
        UniqueConstraint(
            "message_id",
            "resource",
            "reading_type",
            name="uq_measurements_message_resource_reading",
        ),
        Index("ix_measurements_station_timestamp", "charging_point_id", "observed_at"),
        Index("ix_measurements_session_resource", "session_id", "resource"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("processed_messages.message_id", ondelete="RESTRICT"), nullable=False
    )
    charging_point_id: Mapped[str] = mapped_column(
        ForeignKey("charging_points.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("charging_sessions.id", ondelete="RESTRICT")
    )
    resource: Mapped[str] = mapped_column(String(24), nullable=False)
    unit: Mapped[str] = mapped_column(String(24), nullable=False)
    reading_type: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    session: Mapped[ChargingSession | None] = relationship(back_populates="measurements")


class Threshold(Base):
    __tablename__ = "thresholds"
    __table_args__ = (
        CheckConstraint(
            "resource IN ('ELECTRICITE', 'EAU', 'GAZ')", name="ck_thresholds_resource"
        ),
        CheckConstraint(
            "alert_quantity IS NULL OR alert_quantity >= 0",
            name="ck_thresholds_alert_quantity",
        ),
        CheckConstraint(
            "cutoff_quantity IS NULL OR cutoff_quantity >= 0",
            name="ck_thresholds_cutoff_quantity",
        ),
        CheckConstraint(
            "alert_quantity IS NULL OR cutoff_quantity IS NULL "
            "OR cutoff_quantity >= alert_quantity",
            name="ck_thresholds_quantity_order",
        ),
        CheckConstraint(
            "alert_cost IS NULL OR alert_cost >= 0", name="ck_thresholds_alert_cost"
        ),
        CheckConstraint(
            "cutoff_cost IS NULL OR cutoff_cost >= 0", name="ck_thresholds_cutoff_cost"
        ),
        CheckConstraint(
            "alert_cost IS NULL OR cutoff_cost IS NULL OR cutoff_cost >= alert_cost",
            name="ck_thresholds_cost_order",
        ),
        UniqueConstraint("charging_point_id", "resource", name="uq_threshold_point_resource"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    charging_point_id: Mapped[str] = mapped_column(
        ForeignKey("charging_points.id", ondelete="CASCADE"), nullable=False
    )
    resource: Mapped[str] = mapped_column(String(24), nullable=False)
    alert_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    cutoff_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    alert_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    cutoff_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))


class ThresholdEvent(Base):
    __tablename__ = "threshold_events"
    __table_args__ = (
        CheckConstraint(
            "resource IN ('ELECTRICITE', 'EAU', 'GAZ')",
            name="ck_threshold_events_resource",
        ),
        CheckConstraint(
            "event_type IN ('alert', 'cutoff')",
            name="ck_threshold_events_type",
        ),
        UniqueConstraint(
            "session_id",
            "resource",
            "event_type",
            name="uq_threshold_event_session_resource_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("charging_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    charging_point_id: Mapped[str] = mapped_column(
        ForeignKey("charging_points.id", ondelete="RESTRICT"), nullable=False
    )
    resource: Mapped[str] = mapped_column(String(24), nullable=False)
    event_type: Mapped[str] = mapped_column(String(12), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DeviceCommand(Base):
    __tablename__ = "device_commands"
    __table_args__ = (
        CheckConstraint(
            "command IN ('activate', 'cut_off')", name="ck_device_commands_command"
        ),
        CheckConstraint(
            "status IN ('pending', 'sent', 'confirmed', 'rejected', 'timed_out')",
            name="ck_device_commands_status",
        ),
    )

    command_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    charging_point_id: Mapped[str] = mapped_column(
        ForeignKey("charging_points.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("charging_sessions.id", ondelete="RESTRICT")
    )
    command: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_payload: Mapped[dict | None] = mapped_column(JSON)
