"""Validation, déduplication et persistance transactionnelle des messages MQTT."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.messages import (
    CommandResponse,
    StateMessage,
    TelemetryMessage,
    parse_command_response,
    parse_state,
    parse_telemetry,
)
from backend.domain.entities import TariffTier as DomainTariffTier
from backend.domain.tariffs import calculate_progressive_cost
from backend.infrastructure.database.models import (
    ChargingPoint,
    ChargingSession,
    DeviceCommand,
    Measurement,
    ProcessedMessage,
    Reservation,
    ReservationTariff,
    Tariff,
    TariffTier,
    Threshold,
    ThresholdEvent,
)

log = logging.getLogger(__name__)


class SqlAlchemyMessageProcessor:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def process(self, topic: str, raw_payload: str) -> bool:
        if topic.endswith("/telemetrie"):
            return self._process_telemetry(parse_telemetry(topic, raw_payload))
        if topic.endswith("/etat"):
            return self._process_state(parse_state(topic, raw_payload))
        if topic.endswith("/reponse"):
            return self._process_command_response(
                parse_command_response(topic, raw_payload)
            )
        raise ValueError("Topic MQTT non pris en charge.")

    def _process_telemetry(self, message: TelemetryMessage) -> bool:
        with self._session_factory() as session, session.begin():
            if not self._claim_message(
                session, message.message_id, "telemetry", message.payload
            ):
                return False
            self._upsert_point(
                session,
                message.charging_point_id,
                online=True,
                plug_active=message.plug_active,
                last_seen_at=message.timestamp_utc,
            )
            active_session = self._valid_active_session(session, message)
            if (
                not message.plug_active
                and message.session_id is not None
                and active_session is None
            ):
                reported_session = session.get(ChargingSession, message.session_id)
                if (
                    reported_session is not None
                    and reported_session.status == "active"
                    and reported_session.charging_point_id == message.charging_point_id
                ):
                    reported_session.status = "inactive"
                    reported_session.ended_at = message.timestamp_utc
            for resource, reading in message.readings.items():
                if reading.reading_type == "index":
                    index_session_id = (
                        active_session.id
                        if active_session is not None and message.plug_active
                        else None
                    )
                    previous = self._latest_index(
                        session,
                        message.charging_point_id,
                        resource.value,
                        index_session_id,
                    )
                    if previous is not None and message.timestamp_utc < _as_utc(previous.observed_at):
                        log.warning(
                            "Relevé ancien ignoré pour %s/%s (message %s)",
                            message.charging_point_id,
                            resource.value,
                            message.message_id,
                        )
                        continue
                    delta = reading.value - (
                        previous.value if previous is not None else Decimal("0")
                    )
                    session.add(
                        Measurement(
                            message_id=message.message_id,
                            charging_point_id=message.charging_point_id,
                            session_id=index_session_id,
                            resource=resource.value,
                            unit=reading.unit,
                            reading_type="index",
                            value=reading.value,
                            observed_at=message.timestamp_utc,
                            raw_payload=message.payload,
                        )
                    )
                    if delta < 0:
                        log.warning(
                            "Index compteur réinitialisé pour %s/%s; delta non facturé.",
                            message.charging_point_id,
                            resource.value,
                        )
                    elif delta > 0 and active_session is not None and message.plug_active:
                        session.add(
                            Measurement(
                                message_id=message.message_id,
                                charging_point_id=message.charging_point_id,
                                session_id=active_session.id,
                                resource=resource.value,
                                unit=reading.unit,
                                reading_type="delta",
                                value=delta,
                                observed_at=message.timestamp_utc,
                                raw_payload=message.payload,
                            )
                        )
                        session.flush()
                        self._check_thresholds(
                            session,
                            active_session,
                            resource.value,
                            message.timestamp_utc,
                        )
                else:
                    session.add(
                        Measurement(
                            message_id=message.message_id,
                            charging_point_id=message.charging_point_id,
                            session_id=(
                                active_session.id
                                if active_session is not None and message.plug_active
                                else None
                            ),
                            resource=resource.value,
                            unit=reading.unit,
                            reading_type="delta",
                            value=reading.value,
                            observed_at=message.timestamp_utc,
                            raw_payload=message.payload,
                        )
                    )
                    if (
                        active_session is not None
                        and message.plug_active
                        and reading.value > 0
                    ):
                        session.flush()
                        self._check_thresholds(
                            session,
                            active_session,
                            resource.value,
                            message.timestamp_utc,
                        )
            return True

    def _process_state(self, message: StateMessage) -> bool:
        with self._session_factory() as session, session.begin():
            if not self._claim_message(session, message.message_id, "state", message.payload):
                return False
            prior = session.get(ChargingPoint, message.charging_point_id)
            plug_active = message.plug_active if message.status == "online" else (
                prior.plug_active if prior is not None else False
            )
            self._upsert_point(
                session,
                message.charging_point_id,
                online=message.status == "online",
                plug_active=plug_active,
                last_seen_at=message.timestamp_utc,
            )
            return True

    def _process_command_response(self, message: CommandResponse) -> bool:
        with self._session_factory() as session, session.begin():
            if not self._claim_message(
                session, message.message_id, "command_response", message.payload
            ):
                return False
            self._upsert_point(
                session,
                message.charging_point_id,
                online=True,
                plug_active=message.plug_active,
                last_seen_at=message.timestamp_utc,
            )
            command = session.get(DeviceCommand, message.command_id)
            if (
                command is None
                or command.charging_point_id != message.charging_point_id
                or command.command != message.command
                or command.status not in {"pending", "sent"}
            ):
                log.warning(
                    "Réponse MQTT sans commande en attente: %s", message.command_id
                )
                return True
            command.status = message.status
            command.responded_at = message.timestamp_utc
            command.response_payload = message.payload
            if command.session_id is not None:
                charging_session = session.get(ChargingSession, command.session_id)
                if charging_session is not None:
                    if message.command == "activate":
                        charging_session.status = (
                            "active"
                            if message.status == "confirmed" and message.plug_active
                            else "failed"
                        )
                        if charging_session.status == "active":
                            charging_session.started_at = message.timestamp_utc
                            charging_session.reservation.status = "active"
                    elif message.status == "confirmed":
                        charging_session.status = "inactive"
                        charging_session.ended_at = message.timestamp_utc
            return True

    @staticmethod
    def _claim_message(
        session: Session, message_id: UUID, message_type: str, payload: dict
    ) -> bool:
        values = {
            "message_id": message_id,
            "message_type": message_type,
            "payload": payload,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = pg_insert(ProcessedMessage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=[ProcessedMessage.message_id]
            )
            statement = statement.returning(ProcessedMessage.message_id)
            return session.execute(statement).scalar_one_or_none() is not None
        if dialect == "sqlite":
            statement = sqlite_insert(ProcessedMessage).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=[ProcessedMessage.message_id]
            )
            statement = statement.returning(ProcessedMessage.message_id)
            return session.execute(statement).scalar_one_or_none() is not None
        if session.get(ProcessedMessage, message_id) is not None:
            return False
        session.add(ProcessedMessage(**values))
        session.flush()
        return True

    @staticmethod
    def _upsert_point(
        session: Session,
        charging_point_id: str,
        online: bool,
        plug_active: bool,
        last_seen_at: datetime,
    ) -> None:
        point = session.get(ChargingPoint, charging_point_id)
        if point is None:
            session.add(
                ChargingPoint(
                    id=charging_point_id,
                    name=f"Borne {charging_point_id}",
                    is_online=online,
                    plug_active=plug_active,
                    last_seen_at=last_seen_at,
                )
            )
        elif point.last_seen_at is None or _as_utc(point.last_seen_at) <= last_seen_at:
            point.is_online = online
            point.plug_active = plug_active
            point.last_seen_at = last_seen_at

    @staticmethod
    def _latest_index(
        session: Session,
        charging_point_id: str,
        resource: str,
        session_id: UUID | None,
    ) -> Measurement | None:
        return session.scalar(
            select(Measurement)
            .where(
                Measurement.charging_point_id == charging_point_id,
                Measurement.resource == resource,
                Measurement.reading_type == "index",
                Measurement.session_id.is_(session_id)
                if session_id is None
                else Measurement.session_id == session_id,
            )
            .order_by(Measurement.observed_at.desc(), Measurement.id.desc())
            .limit(1)
        )

    @staticmethod
    def _valid_active_session(
        session: Session, message: TelemetryMessage
    ) -> ChargingSession | None:
        if (
            not message.plug_active
            or message.session_id is None
            or message.reservation_id is None
        ):
            return None
        charging_session = session.get(ChargingSession, message.session_id)
        if (
            charging_session is None
            or charging_session.status != "active"
            or charging_session.charging_point_id != message.charging_point_id
            or charging_session.reservation_id != message.reservation_id
        ):
            return None
        reservation = session.get(Reservation, charging_session.reservation_id)
        if (
            reservation is None
            or reservation.status != "active"
            or message.timestamp_utc < _as_utc(reservation.starts_at)
            or message.timestamp_utc >= _as_utc(reservation.ends_at)
        ):
            return None
        return charging_session

    @staticmethod
    def _check_thresholds(
        session: Session,
        charging_session: ChargingSession,
        resource: str,
        observed_at: datetime,
    ) -> None:
        threshold = session.scalar(
            select(Threshold).where(
                Threshold.charging_point_id == charging_session.charging_point_id,
                Threshold.resource == resource,
            )
        )
        if threshold is None:
            return
        quantity = session.scalar(
            select(func.sum(Measurement.value)).where(
                Measurement.session_id == charging_session.id,
                Measurement.resource == resource,
                Measurement.reading_type == "delta",
            )
        ) or Decimal("0")
        cost = SqlAlchemyMessageProcessor._session_resource_cost(
            session, charging_session.reservation_id, resource, quantity
        )
        alert_reached = (
            threshold.alert_quantity is not None
            and quantity >= threshold.alert_quantity
        ) or (
            threshold.alert_cost is not None
            and cost is not None
            and cost >= threshold.alert_cost
        )
        cutoff_reached = (
            threshold.cutoff_quantity is not None
            and quantity >= threshold.cutoff_quantity
        ) or (
            threshold.cutoff_cost is not None
            and cost is not None
            and cost >= threshold.cutoff_cost
        )
        if alert_reached:
            alert_created = SqlAlchemyMessageProcessor._record_threshold_event(
                session, charging_session, resource, "alert", quantity, cost
            )
            if alert_created:
                log.warning(
                    "Seuil d'alerte atteint: borne=%s ressource=%s quantité=%s coût=%s",
                    charging_session.charging_point_id,
                    resource,
                    quantity,
                    cost,
                )
        if cutoff_reached:
            SqlAlchemyMessageProcessor._record_threshold_event(
                session, charging_session, resource, "cutoff", quantity, cost
            )
            pending_command = session.scalar(
                select(DeviceCommand.command_id)
                .where(
                    DeviceCommand.session_id == charging_session.id,
                    DeviceCommand.command == "cut_off",
                    DeviceCommand.status.in_(("pending", "sent", "confirmed")),
                )
                .limit(1)
            )
            if pending_command is None:
                session.add(
                    DeviceCommand(
                        command_id=uuid4(),
                        charging_point_id=charging_session.charging_point_id,
                        session_id=charging_session.id,
                        command="cut_off",
                        status="pending",
                        payload={
                            "session_id": str(charging_session.id),
                            "id_reservation": str(charging_session.reservation_id),
                        },
                        expires_at=observed_at + timedelta(seconds=20),
                    )
                )
                log.error(
                    "Seuil de coupure atteint; commande MQTT créée pour borne %s.",
                    charging_session.charging_point_id,
                )

    @staticmethod
    def _record_threshold_event(
        session: Session,
        charging_session: ChargingSession,
        resource: str,
        event_type: str,
        quantity: Decimal,
        cost: Decimal | None,
    ) -> bool:
        existing = session.scalar(
            select(ThresholdEvent.id).where(
                ThresholdEvent.session_id == charging_session.id,
                ThresholdEvent.resource == resource,
                ThresholdEvent.event_type == event_type,
            )
        )
        if existing is None:
            session.add(
                ThresholdEvent(
                    session_id=charging_session.id,
                    charging_point_id=charging_session.charging_point_id,
                    resource=resource,
                    event_type=event_type,
                    quantity=quantity,
                    cost=cost,
                )
            )
            return True
        return False

    @staticmethod
    def _session_resource_cost(
        session: Session,
        reservation_id: UUID,
        resource: str,
        quantity: Decimal,
    ) -> Decimal | None:
        tariff_id = session.scalar(
            select(ReservationTariff.tariff_id).where(
                ReservationTariff.reservation_id == reservation_id,
                ReservationTariff.resource == resource,
            )
        )
        tariff = session.get(Tariff, tariff_id) if tariff_id is not None else None
        if tariff is None:
            return None
        tiers = session.scalars(
            select(TariffTier)
            .where(TariffTier.tariff_id == tariff.id)
            .order_by(TariffTier.lower_bound)
        ).all()
        return calculate_progressive_cost(
            quantity,
            [
                DomainTariffTier(
                    lower_bound=tier.lower_bound,
                    upper_bound=tier.upper_bound,
                    unit_price=tier.unit_price,
                )
                for tier in tiers
            ],
            tariff.currency,
        ).amount


class CommandOutbox:
    """Publie et réessaie les commandes enregistrées par l'API."""

    def __init__(self, session_factory: sessionmaker, retry_seconds: int = 2) -> None:
        self._session_factory = session_factory
        self._retry_interval = timedelta(seconds=retry_seconds)

    def pending(self, now: datetime | None = None) -> list[DeviceCommand]:
        current = now or datetime.now(timezone.utc)
        commands: list[DeviceCommand] = []
        with self._session_factory() as session, session.begin():
            candidates = session.scalars(
                select(DeviceCommand)
                .where(DeviceCommand.status.in_(("pending", "sent")))
                .order_by(DeviceCommand.requested_at)
                .limit(100)
            ).all()
            for command in candidates:
                if command.expires_at is not None and _as_utc(command.expires_at) <= current:
                    command.status = "timed_out"
                    if command.session_id is not None:
                        charging_session = session.get(
                            ChargingSession, command.session_id
                        )
                        if charging_session is not None:
                            charging_session.status = "failed"
                    continue
                if (
                    command.status == "pending"
                    or command.last_sent_at is None
                    or _as_utc(command.last_sent_at) + self._retry_interval <= current
                ):
                    commands.append(command)
            session.flush()
            session.expunge_all()
        return commands

    def mark_sent(self, command_id: UUID, sent_at: datetime | None = None) -> None:
        with self._session_factory() as session, session.begin():
            command = session.get(DeviceCommand, command_id)
            if command is None or command.status not in {"pending", "sent"}:
                return
            command.status = "sent"
            command.attempts += 1
            command.last_sent_at = sent_at or datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
