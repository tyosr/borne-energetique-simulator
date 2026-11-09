"""Adaptateurs SQLAlchemy des ports métier."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.domain.entities import (
    ActivationRequest,
    AccessTag,
    ChargingSessionState,
    ChargingPointState,
    Consumer,
    ConflictError,
    ConsumptionTotal,
    DeviceCommandStatus,
    Measurement,
    Reservation,
    ReservationStatus,
    Resource,
    Tariff,
    TariffTier,
    ThresholdEventRecord,
    ThresholdConfig,
)
from backend.infrastructure.database.models import (
    AccessTag as AccessTagModel,
    ChargingPoint,
    ChargingSession,
    Consumer as ConsumerModel,
    DeviceCommand,
    Measurement as MeasurementModel,
    Reservation as ReservationModel,
    ReservationTariff,
    Tariff as TariffModel,
    TariffTier as TariffTierModel,
    Threshold as ThresholdModel,
    ThresholdEvent as ThresholdEventModel,
)


class SqlAlchemyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_consumer(self, display_name: str, email: str | None) -> Consumer:
        model = ConsumerModel(display_name=display_name, email=email)
        self._session.add(model)
        self._session.flush()
        return Consumer(id=model.id, display_name=model.display_name, email=model.email)

    def consumer_exists(self, consumer_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(ConsumerModel.id).where(ConsumerModel.id == consumer_id)
            )
            is not None
        )

    def create_tag(
        self, consumer_id: UUID, code: str, expires_at: datetime | None
    ) -> AccessTag:
        model = AccessTagModel(
            consumer_id=consumer_id,
            code=code,
            expires_at=expires_at,
            status="active",
        )
        self._session.add(model)
        self._session.flush()
        return self._to_access_tag(model)

    def find_by_code(self, code: str) -> AccessTag | None:
        model = self._session.scalar(
            select(AccessTagModel).where(AccessTagModel.code == code)
        )
        return self._to_access_tag(model) if model is not None else None

    def set_status(self, code: str, status: str) -> AccessTag:
        model = self._session.scalar(
            select(AccessTagModel).where(AccessTagModel.code == code)
        )
        if model is None:
            raise LookupError("Tag introuvable.")
        model.status = status
        self._session.flush()
        return self._to_access_tag(model)

    def activate(
        self, tag: AccessTag, charging_point_id: str, now: datetime
    ) -> ActivationRequest:
        point = self._session.scalar(
            select(ChargingPoint)
            .where(ChargingPoint.id == charging_point_id)
            .with_for_update()
        )
        if point is None:
            raise LookupError("Borne introuvable.")
        reservation = self._session.scalar(
            select(ReservationModel)
            .where(
                ReservationModel.consumer_id == tag.consumer_id,
                ReservationModel.charging_point_id == charging_point_id,
                ReservationModel.status.in_(("pending", "active")),
                ReservationModel.starts_at <= now,
                ReservationModel.ends_at > now,
            )
            .order_by(ReservationModel.starts_at)
            .limit(1)
        )
        if reservation is None:
            raise PermissionError("Aucune réservation valide pour ce tag et cette borne.")
        existing_session = self._session.scalar(
            select(ChargingSession.id)
            .where(
                ChargingSession.charging_point_id == charging_point_id,
                ChargingSession.status.in_(("pending_activation", "active")),
            )
            .limit(1)
        )
        if existing_session is not None:
            raise ConflictError("La borne possède déjà une session active ou en attente.")

        session_id = uuid4()
        command_id = uuid4()
        self._session.add(
            ChargingSession(
                id=session_id,
                reservation_id=reservation.id,
                charging_point_id=charging_point_id,
                status="pending_activation",
            )
        )
        self._session.add(
            DeviceCommand(
                command_id=command_id,
                charging_point_id=charging_point_id,
                session_id=session_id,
                command="activate",
                status="pending",
                payload={
                    "session_id": str(session_id),
                    "id_reservation": str(reservation.id),
                },
                expires_at=now + timedelta(seconds=20),
            )
        )
        self._session.flush()
        return ActivationRequest(
            command_id=command_id,
            session_id=session_id,
            reservation_id=reservation.id,
            status="pending_activation",
        )

    def get_command(self, command_id: UUID) -> DeviceCommandStatus | None:
        model = self._session.get(DeviceCommand, command_id)
        if model is None:
            return None
        return DeviceCommandStatus(
            command_id=model.command_id,
            command=model.command,
            status=model.status,
            charging_point_id=model.charging_point_id,
        )

    def get_session(self, session_id: UUID) -> ChargingSessionState | None:
        model = self._session.get(ChargingSession, session_id)
        if model is None:
            return None
        return ChargingSessionState(
            id=model.id,
            reservation_id=model.reservation_id,
            charging_point_id=model.charging_point_id,
            status=model.status,
            started_at=model.started_at,
            ended_at=model.ended_at,
        )

    def create_if_available(
        self,
        consumer_id: UUID,
        charging_point_id: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Reservation:
        station = self._session.scalar(
            select(ChargingPoint)
            .where(ChargingPoint.id == charging_point_id)
            .with_for_update()
        )
        if station is None:
            raise LookupError("Borne introuvable.")
        if not self.consumer_exists(consumer_id):
            raise LookupError("Consommateur introuvable.")

        overlap = self._session.scalar(
            select(ReservationModel.id)
            .where(
                ReservationModel.charging_point_id == charging_point_id,
                ReservationModel.status.in_(("pending", "active")),
                ReservationModel.starts_at < ends_at,
                ReservationModel.ends_at > starts_at,
            )
            .limit(1)
        )
        if overlap is not None:
            raise ConflictError("La borne possède déjà une réservation sur cette période.")

        model = ReservationModel(
            consumer_id=consumer_id,
            charging_point_id=charging_point_id,
            starts_at=starts_at,
            ends_at=ends_at,
            status=ReservationStatus.PENDING.value,
        )
        self._session.add(model)
        self._session.flush()
        return self._to_reservation(model)

    def list_for_point(
        self, charging_point_id: str, offset: int, limit: int
    ) -> list[Reservation]:
        models = self._session.scalars(
            select(ReservationModel)
            .where(ReservationModel.charging_point_id == charging_point_id)
            .order_by(ReservationModel.starts_at, ReservationModel.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [self._to_reservation(model) for model in models]

    def get_reservation(self, reservation_id: UUID) -> Reservation | None:
        model = self._session.get(ReservationModel, reservation_id)
        return self._to_reservation(model) if model is not None else None

    def get_for_reservation(
        self, reservation_id: UUID, resource: str
    ) -> Tariff | None:
        model = self._session.scalar(
            select(TariffModel)
            .join(ReservationTariff, ReservationTariff.tariff_id == TariffModel.id)
            .where(
                ReservationTariff.reservation_id == reservation_id,
                ReservationTariff.resource == resource,
            )
            .options(selectinload(TariffModel.tiers))
        )
        if model is None:
            return None
        return Tariff(
            id=model.id,
            name=model.name,
            resource=Resource(model.resource),
            version=model.version,
            currency=model.currency,
            tiers=tuple(
                TariffTier(
                    lower_bound=tier.lower_bound,
                    upper_bound=tier.upper_bound,
                    unit_price=tier.unit_price,
                )
                for tier in model.tiers
            ),
        )

    def create_tariff(
        self,
        name: str,
        resource: str,
        version: int,
        currency: str,
        tiers: list[TariffTier],
    ) -> Tariff:
        model = TariffModel(
            name=name,
            resource=resource,
            version=version,
            currency=currency,
            enabled=True,
            tiers=[
                TariffTierModel(
                    lower_bound=tier.lower_bound,
                    upper_bound=tier.upper_bound,
                    unit_price=tier.unit_price,
                )
                for tier in tiers
            ],
        )
        self._session.add(model)
        self._session.flush()
        return Tariff(
            id=model.id,
            name=model.name,
            resource=Resource(model.resource),
            version=model.version,
            currency=model.currency,
            tiers=tuple(
                TariffTier(
                    lower_bound=tier.lower_bound,
                    upper_bound=tier.upper_bound,
                    unit_price=tier.unit_price,
                )
                for tier in model.tiers
            ),
        )

    def assign_to_reservation(
        self, reservation_id: UUID, resource: str, tariff_id: UUID
    ) -> None:
        reservation = self._session.get(ReservationModel, reservation_id)
        if reservation is None:
            raise LookupError("Réservation introuvable.")
        tariff = self._session.get(TariffModel, tariff_id)
        if tariff is None:
            raise LookupError("Tarif introuvable.")
        if tariff.resource != resource or not tariff.enabled:
            raise ConflictError("Le tarif est désactivé ou ne correspond pas à la ressource.")
        self._session.add(
            ReservationTariff(
                reservation_id=reservation_id,
                resource=resource,
                tariff_id=tariff_id,
            )
        )
        self._session.flush()

    def get_charging_point_state(self, charging_point_id: str) -> ChargingPointState | None:
        model = self._session.get(ChargingPoint, charging_point_id)
        if model is None:
            return None
        return ChargingPointState(
            id=model.id,
            name=model.name,
            is_online=model.is_online,
            plug_active=model.plug_active,
            last_seen_at=model.last_seen_at,
        )

    def list_measurements(
        self,
        charging_point_id: str,
        resource: str | None,
        session_id: UUID | None,
        offset: int,
        limit: int,
    ) -> list[Measurement]:
        statement = select(MeasurementModel).where(
            MeasurementModel.charging_point_id == charging_point_id
        )
        if resource is not None:
            statement = statement.where(MeasurementModel.resource == resource)
        if session_id is not None:
            statement = statement.where(MeasurementModel.session_id == session_id)
        models = self._session.scalars(
            statement.order_by(
                MeasurementModel.observed_at.desc(), MeasurementModel.id
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            Measurement(
                id=model.id,
                message_id=model.message_id,
                charging_point_id=model.charging_point_id,
                resource=Resource(model.resource),
                unit=model.unit,
                reading_type=model.reading_type,
                value=model.value,
                observed_at=model.observed_at,
                session_id=model.session_id,
            )
            for model in models
        ]

    def get_session_consumption(
        self, session_id: UUID
    ) -> list[ConsumptionTotal] | None:
        if self._session.get(ChargingSession, session_id) is None:
            return None
        rows = self._session.execute(
            select(
                MeasurementModel.resource,
                MeasurementModel.unit,
                func.sum(MeasurementModel.value),
            )
            .where(
                MeasurementModel.session_id == session_id,
                MeasurementModel.reading_type == "delta",
            )
            .group_by(MeasurementModel.resource, MeasurementModel.unit)
            .order_by(MeasurementModel.resource)
        ).all()
        return [
            ConsumptionTotal(
                resource=Resource(resource),
                unit=unit,
                quantity=quantity,
            )
            for resource, unit, quantity in rows
        ]

    def list_threshold_events(
        self, session_id: UUID
    ) -> list[ThresholdEventRecord] | None:
        if self._session.get(ChargingSession, session_id) is None:
            return None
        models = self._session.scalars(
            select(ThresholdEventModel)
            .where(ThresholdEventModel.session_id == session_id)
            .order_by(ThresholdEventModel.created_at, ThresholdEventModel.id)
        ).all()
        return [
            ThresholdEventRecord(
                id=model.id,
                session_id=model.session_id,
                charging_point_id=model.charging_point_id,
                resource=Resource(model.resource),
                event_type=model.event_type,
                quantity=model.quantity,
                cost=model.cost,
                created_at=model.created_at,
            )
            for model in models
        ]

    def set_threshold(self, threshold: ThresholdConfig) -> ThresholdConfig:
        if self._session.get(ChargingPoint, threshold.charging_point_id) is None:
            raise LookupError("Borne introuvable.")
        model = self._session.scalar(
            select(ThresholdModel).where(
                ThresholdModel.charging_point_id == threshold.charging_point_id,
                ThresholdModel.resource == threshold.resource.value,
            )
        )
        if model is None:
            model = ThresholdModel(
                charging_point_id=threshold.charging_point_id,
                resource=threshold.resource.value,
            )
            self._session.add(model)
        model.alert_quantity = threshold.alert_quantity
        model.cutoff_quantity = threshold.cutoff_quantity
        model.alert_cost = threshold.alert_cost
        model.cutoff_cost = threshold.cutoff_cost
        self._session.flush()
        return threshold

    def list_thresholds(self, charging_point_id: str) -> list[ThresholdConfig]:
        models = self._session.scalars(
            select(ThresholdModel)
            .where(ThresholdModel.charging_point_id == charging_point_id)
            .order_by(ThresholdModel.resource)
        ).all()
        return [
            ThresholdConfig(
                charging_point_id=model.charging_point_id,
                resource=Resource(model.resource),
                alert_quantity=model.alert_quantity,
                cutoff_quantity=model.cutoff_quantity,
                alert_cost=model.alert_cost,
                cutoff_cost=model.cutoff_cost,
            )
            for model in models
        ]

    @staticmethod
    def _to_access_tag(model: AccessTagModel) -> AccessTag:
        return AccessTag(
            id=model.id,
            consumer_id=model.consumer_id,
            code=model.code,
            status=model.status,
            expires_at=model.expires_at,
        )

    @staticmethod
    def _to_reservation(model: ReservationModel) -> Reservation:
        return Reservation(
            id=model.id,
            consumer_id=model.consumer_id,
            charging_point_id=model.charging_point_id,
            starts_at=model.starts_at,
            ends_at=model.ends_at,
            status=ReservationStatus(model.status),
        )
