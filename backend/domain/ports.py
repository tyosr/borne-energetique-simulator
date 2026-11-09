"""Ports nécessaires aux cas d'utilisation du backend."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from backend.domain.entities import (
    ActivationRequest,
    AccessTag,
    ChargingSessionState,
    ChargingPointState,
    Consumer,
    ConsumptionTotal,
    DeviceCommandStatus,
    Measurement,
    Reservation,
    Tariff,
    TariffTier,
    ThresholdEventRecord,
    ThresholdConfig,
)


class ConsumerPort(Protocol):
    def create_consumer(self, display_name: str, email: str | None) -> Consumer: ...

    def consumer_exists(self, consumer_id: UUID) -> bool: ...


class AccessTagPort(Protocol):
    def create_tag(
        self, consumer_id: UUID, code: str, expires_at: datetime | None
    ) -> AccessTag: ...

    def find_by_code(self, code: str) -> AccessTag | None: ...

    def set_status(self, code: str, status: str) -> AccessTag: ...


class ActivationPort(Protocol):
    def activate(
        self, tag: AccessTag, charging_point_id: str, now: datetime
    ) -> ActivationRequest: ...

    def get_command(self, command_id: UUID) -> DeviceCommandStatus | None: ...

    def get_session(self, session_id: UUID) -> ChargingSessionState | None: ...


class ReservationPort(Protocol):
    def create_if_available(
        self,
        consumer_id: UUID,
        charging_point_id: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Reservation: ...

    def list_for_point(
        self, charging_point_id: str, offset: int, limit: int
    ) -> list[Reservation]: ...


class TariffPort(Protocol):
    def get_for_reservation(
        self, reservation_id: UUID, resource: str
    ) -> Tariff | None: ...

    def create_tariff(
        self,
        name: str,
        resource: str,
        version: int,
        currency: str,
        tiers: list[TariffTier],
    ) -> Tariff: ...

    def assign_to_reservation(
        self, reservation_id: UUID, resource: str, tariff_id: UUID
    ) -> None: ...


class ReadModelPort(Protocol):
    def get_charging_point_state(self, charging_point_id: str) -> ChargingPointState | None: ...

    def list_measurements(
        self,
        charging_point_id: str,
        resource: str | None,
        session_id: UUID | None,
        offset: int,
        limit: int,
    ) -> list[Measurement]: ...

    def get_session_consumption(self, session_id: UUID) -> list[ConsumptionTotal] | None: ...

    def list_threshold_events(
        self, session_id: UUID
    ) -> list[ThresholdEventRecord] | None: ...


class ThresholdPort(Protocol):
    def set_threshold(self, threshold: ThresholdConfig) -> ThresholdConfig: ...

    def list_thresholds(self, charging_point_id: str) -> list[ThresholdConfig]: ...
