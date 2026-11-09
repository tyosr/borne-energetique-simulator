"""Objets métier indépendants des frameworks et de la persistance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class ConflictError(Exception):
    """Conflit métier empêchant la création ou l'activation d'une ressource."""


class Resource(str, Enum):
    ELECTRICITE = "ELECTRICITE"
    EAU = "EAU"
    GAZ = "GAZ"


class ReservationStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TariffTier:
    lower_bound: Decimal
    upper_bound: Decimal | None
    unit_price: Decimal


@dataclass(frozen=True)
class Tariff:
    id: UUID
    name: str
    resource: Resource
    version: int
    currency: str
    tiers: tuple[TariffTier, ...]


@dataclass(frozen=True)
class TierCharge:
    lower_bound: Decimal
    upper_bound: Decimal | None
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


@dataclass(frozen=True)
class CostEstimate:
    quantity: Decimal
    currency: str
    amount: Decimal
    breakdown: tuple[TierCharge, ...]


@dataclass(frozen=True)
class Consumer:
    id: UUID
    display_name: str
    email: str | None


@dataclass(frozen=True)
class AccessTag:
    id: UUID
    consumer_id: UUID
    code: str
    status: str
    expires_at: datetime | None


@dataclass(frozen=True)
class Reservation:
    id: UUID
    consumer_id: UUID
    charging_point_id: str
    starts_at: datetime
    ends_at: datetime
    status: ReservationStatus


@dataclass(frozen=True)
class Measurement:
    id: UUID
    message_id: UUID
    charging_point_id: str
    resource: Resource
    unit: str
    reading_type: str
    value: Decimal
    observed_at: datetime
    session_id: UUID | None = None


@dataclass(frozen=True)
class ChargingPointState:
    id: str
    name: str
    is_online: bool
    plug_active: bool
    last_seen_at: datetime | None


@dataclass(frozen=True)
class ConsumptionTotal:
    resource: Resource
    unit: str
    quantity: Decimal


@dataclass(frozen=True)
class ThresholdConfig:
    charging_point_id: str
    resource: Resource
    alert_quantity: Decimal | None
    cutoff_quantity: Decimal | None
    alert_cost: Decimal | None
    cutoff_cost: Decimal | None


@dataclass(frozen=True)
class ActivationRequest:
    command_id: UUID
    session_id: UUID
    reservation_id: UUID
    status: str


@dataclass(frozen=True)
class DeviceCommandStatus:
    command_id: UUID
    command: str
    status: str
    charging_point_id: str


@dataclass(frozen=True)
class ChargingSessionState:
    id: UUID
    reservation_id: UUID
    charging_point_id: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True)
class ThresholdEventRecord:
    id: UUID
    session_id: UUID
    charging_point_id: str
    resource: Resource
    event_type: str
    quantity: Decimal
    cost: Decimal | None
    created_at: datetime
