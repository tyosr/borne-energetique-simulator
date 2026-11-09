"""Schémas HTTP d'entrée et de sortie."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConsumerCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)


class ConsumerRead(BaseModel):
    id: UUID
    display_name: str
    email: str | None


class TagCreate(BaseModel):
    consumer_id: UUID
    code: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = None


class TagRead(BaseModel):
    id: UUID
    consumer_id: UUID
    code: str
    status: str
    expires_at: datetime | None


class TagStatusUpdate(BaseModel):
    status: str


class SimulatedTagPresentation(BaseModel):
    tag_code: str = Field(min_length=1, max_length=128)


class ActivationQueued(BaseModel):
    command_id: UUID
    session_id: UUID
    reservation_id: UUID
    status: str


class DeviceCommandRead(BaseModel):
    command_id: UUID
    command: str
    status: str
    charging_point_id: str


class ChargingSessionRead(BaseModel):
    id: UUID
    reservation_id: UUID
    charging_point_id: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None


class ReservationCreate(BaseModel):
    consumer_id: UUID
    charging_point_id: str = Field(min_length=1, max_length=64)
    starts_at: datetime
    ends_at: datetime


class ReservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    consumer_id: UUID
    charging_point_id: str
    starts_at: datetime
    ends_at: datetime
    status: str


class ReservationPage(BaseModel):
    items: list[ReservationRead]
    offset: int
    limit: int


class CostEstimateRequest(BaseModel):
    resource: str
    quantity: Decimal = Field(ge=0)


class TariffTierInput(BaseModel):
    lower_bound: Decimal = Field(ge=0)
    upper_bound: Decimal | None = None
    unit_price: Decimal = Field(ge=0)


class TariffCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    resource: str
    version: int = Field(ge=1)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    tiers: list[TariffTierInput] = Field(min_length=1)


class TariffRead(BaseModel):
    id: UUID
    name: str
    resource: str
    version: int
    currency: str
    tiers: list[TariffTierInput]


class ReservationTariffAssignment(BaseModel):
    resource: str
    tariff_id: UUID


class TierChargeRead(BaseModel):
    lower_bound: Decimal
    upper_bound: Decimal | None
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


class CostEstimateRead(BaseModel):
    quantity: Decimal
    currency: str
    amount: Decimal
    breakdown: list[TierChargeRead]


class HealthRead(BaseModel):
    status: str


class ChargingPointStateRead(BaseModel):
    id: str
    name: str
    is_online: bool
    plug_active: bool
    last_seen_at: datetime | None


class MeasurementRead(BaseModel):
    id: UUID
    message_id: UUID
    charging_point_id: str
    session_id: UUID | None
    resource: str
    unit: str
    reading_type: str
    value: Decimal
    observed_at: datetime


class MeasurementPage(BaseModel):
    items: list[MeasurementRead]
    offset: int
    limit: int


class ConsumptionTotalRead(BaseModel):
    resource: str
    unit: str
    quantity: Decimal


class ThresholdUpdate(BaseModel):
    alert_quantity: Decimal | None = Field(default=None, ge=0)
    cutoff_quantity: Decimal | None = Field(default=None, ge=0)
    alert_cost: Decimal | None = Field(default=None, ge=0)
    cutoff_cost: Decimal | None = Field(default=None, ge=0)


class ThresholdRead(ThresholdUpdate):
    charging_point_id: str
    resource: str


class ThresholdEventRead(BaseModel):
    id: UUID
    session_id: UUID
    charging_point_id: str
    resource: str
    event_type: str
    quantity: Decimal
    cost: Decimal | None
    created_at: datetime
