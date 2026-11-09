import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.messages import parse_telemetry
from backend.infrastructure.database.base import Base
from backend.infrastructure.database.models import (
    ChargingPoint,
    ChargingSession,
    Consumer,
    DeviceCommand,
    Measurement,
    ProcessedMessage,
    Reservation,
    Threshold,
    ThresholdEvent,
)
from backend.infrastructure.mqtt.processor import SqlAlchemyMessageProcessor


def telemetry_payload(
    message_id,
    reservation_id,
    session_id,
    timestamp,
    index,
    plug_active=True,
    charging_point_id="A07",
):
    return json.dumps(
        {
            "schema_version": "1.0",
            "message_id": str(message_id),
            "id_borne": charging_point_id,
            "timestamp_utc": timestamp.isoformat(),
            "session_id": str(session_id),
            "id_reservation": str(reservation_id),
            "prise_active": plug_active,
            "ressources": {
                "ELECTRICITE": {
                    "value": str(index),
                    "unit": "kWh",
                    "reading_type": "index",
                }
            },
        }
    )


def test_contract_rejects_topic_payload_station_mismatch():
    payload = telemetry_payload(
        uuid4(), uuid4(), uuid4(), datetime.now(timezone.utc), 1
    )
    with pytest.raises(ValueError, match="id_borne"):
        parse_telemetry("port/bornes/B12/telemetrie", payload)


def test_processor_deduplicates_and_bills_only_active_session_delta():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    consumer_id = uuid4()
    reservation_id = uuid4()
    session_id = uuid4()
    first_at = datetime.now(timezone.utc)

    with factory.begin() as session:
        session.add(Consumer(id=consumer_id, display_name="Test"))
        session.add(ChargingPoint(id="A07", name="Borne A07"))
        session.add(
            Reservation(
                id=reservation_id,
                consumer_id=consumer_id,
                charging_point_id="A07",
                starts_at=first_at - timedelta(minutes=1),
                ends_at=first_at + timedelta(hours=1),
                status="active",
            )
        )
        session.add(
            ChargingSession(
                id=session_id,
                reservation_id=reservation_id,
                charging_point_id="A07",
                status="active",
                started_at=first_at - timedelta(seconds=1),
            )
        )

    processor = SqlAlchemyMessageProcessor(factory)
    topic = "port/bornes/A07/telemetrie"
    first_id = uuid4()
    first_payload = telemetry_payload(
        first_id, reservation_id, session_id, first_at, 1
    )
    assert processor.process(topic, first_payload) is True
    assert processor.process(topic, first_payload) is False

    second_at = first_at + timedelta(seconds=10)
    second_payload = telemetry_payload(
        uuid4(), reservation_id, session_id, second_at, 5
    )
    assert processor.process(topic, second_payload) is True

    cut_off_payload = telemetry_payload(
        uuid4(),
        reservation_id,
        session_id,
        second_at + timedelta(seconds=10),
        109,
        plug_active=False,
    )
    assert processor.process(topic, cut_off_payload) is True

    with factory() as session:
        rows = session.scalars(select(Measurement)).all()
        delta_rows = [row for row in rows if row.reading_type == "delta"]
        assert [row.value for row in delta_rows] == [1, 4]
        assert session.get(ChargingSession, session_id).status == "inactive"
        assert len(session.scalars(select(ProcessedMessage)).all()) == 3


def test_telemetry_creates_unknown_point_and_updates_existing_point_once():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    processor = SqlAlchemyMessageProcessor(factory)
    observed_at = datetime.now(timezone.utc)
    reservation_id = uuid4()
    session_id = uuid4()
    topic = "port/bornes/A07/telemetrie"

    first_id = uuid4()
    first_payload = telemetry_payload(
        first_id, reservation_id, session_id, observed_at, 1
    )
    assert processor.process(topic, first_payload) is True
    assert processor.process(topic, first_payload) is False

    second_id = uuid4()
    second_payload = telemetry_payload(
        second_id,
        reservation_id,
        session_id,
        observed_at + timedelta(seconds=10),
        5,
        plug_active=False,
    )
    assert processor.process(topic, second_payload) is True

    with factory() as session:
        points = session.scalars(select(ChargingPoint)).all()
        assert len(points) == 1
        assert points[0].id == "A07"
        assert points[0].is_online is True
        assert points[0].plug_active is False
        assert points[0].last_seen_at.replace(tzinfo=timezone.utc) == (
            observed_at + timedelta(seconds=10)
        )
        assert session.scalar(select(func.count()).select_from(Measurement)) == 2
        assert session.scalar(select(func.count()).select_from(ProcessedMessage)) == 2


def test_new_telemetry_for_each_station_is_attributed_to_its_topic():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    processor = SqlAlchemyMessageProcessor(factory)
    now = datetime.now(timezone.utc)

    for charging_point_id in ("A07", "B12", "C03"):
        payload = telemetry_payload(
            uuid4(),
            uuid4(),
            uuid4(),
            now,
            0,
            charging_point_id=charging_point_id,
        )
        assert processor.process(
            f"port/bornes/{charging_point_id}/telemetrie", payload
        ) is True

    with factory() as session:
        assert set(session.scalars(select(ChargingPoint.id)).all()) == {
            "A07",
            "B12",
            "C03",
        }
        measurements = session.scalars(select(Measurement)).all()
        assert {measurement.charging_point_id for measurement in measurements} == {
            "A07",
            "B12",
            "C03",
        }


def test_state_messages_still_create_and_update_charging_point():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    processor = SqlAlchemyMessageProcessor(factory)
    now = datetime.now(timezone.utc)
    topic = "port/bornes/A07/etat"

    first_payload = json.dumps(
        {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "id_borne": "A07",
            "timestamp_utc": now.isoformat(),
            "status": "online",
            "prise_active": True,
        }
    )
    assert processor.process(topic, first_payload) is True
    assert processor.process(topic, first_payload) is False

    second_at = now + timedelta(seconds=5)
    second_payload = json.dumps(
        {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "id_borne": "A07",
            "timestamp_utc": second_at.isoformat(),
            "status": "offline",
            "prise_active": False,
        }
    )
    assert processor.process(topic, second_payload) is True

    with factory() as session:
        point = session.get(ChargingPoint, "A07")
        assert point is not None
        assert point.is_online is False
        assert point.plug_active is True
        assert point.last_seen_at.replace(tzinfo=timezone.utc) == second_at


def test_failed_telemetry_rolls_back_message_and_point(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    processor = SqlAlchemyMessageProcessor(factory)
    message_id = uuid4()
    payload = telemetry_payload(
        message_id, uuid4(), uuid4(), datetime.now(timezone.utc), 1
    )

    def fail_upsert(*_args, **_kwargs):
        raise RuntimeError("simulated business failure")

    monkeypatch.setattr(
        SqlAlchemyMessageProcessor, "_upsert_point", staticmethod(fail_upsert)
    )
    with pytest.raises(RuntimeError, match="simulated business failure"):
        processor.process("port/bornes/A07/telemetrie", payload)

    with factory() as session:
        assert session.get(ProcessedMessage, message_id) is None
        assert session.get(ChargingPoint, "A07") is None
        assert session.scalar(select(func.count()).select_from(Measurement)) == 0


@pytest.mark.parametrize(
    ("rowcount", "returned_id", "expected"),
    [(-1, uuid4(), True), (0, None, False)],
)
def test_postgresql_claim_uses_returned_row_not_rowcount(
    rowcount, returned_id, expected
):
    class Result:
        def __init__(self):
            self.rowcount = rowcount

        def scalar_one_or_none(self):
            return returned_id

    class FakeSession:
        def __init__(self):
            self.statement = None

        def get_bind(self):
            class Bind:
                class dialect:
                    name = "postgresql"

            return Bind()

        def execute(self, statement):
            self.statement = statement
            return Result()

    fake_session = FakeSession()
    result = SqlAlchemyMessageProcessor._claim_message(
        fake_session, uuid4(), "telemetry", {}
    )

    assert result is expected
    assert fake_session.statement._returning


def test_confirmed_command_is_the_only_event_that_activates_a_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    consumer_id = uuid4()
    reservation_id = uuid4()
    session_id = uuid4()
    command_id = uuid4()
    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        session.add(Consumer(id=consumer_id, display_name="Command test"))
        session.add(ChargingPoint(id="A07", name="Borne A07"))
        session.add(
            Reservation(
                id=reservation_id,
                consumer_id=consumer_id,
                charging_point_id="A07",
                starts_at=now - timedelta(minutes=1),
                ends_at=now + timedelta(hours=1),
                status="pending",
            )
        )
        session.add(
            ChargingSession(
                id=session_id,
                reservation_id=reservation_id,
                charging_point_id="A07",
                status="pending_activation",
            )
        )
        session.add(
            DeviceCommand(
                command_id=command_id,
                charging_point_id="A07",
                session_id=session_id,
                command="activate",
                status="sent",
                payload={},
            )
        )

    payload = json.dumps(
        {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "command_id": str(command_id),
            "id_borne": "A07",
            "timestamp_utc": now.isoformat(),
            "command": "activate",
            "status": "confirmed",
            "prise_active": True,
        }
    )
    topic = "port/bornes/A07/commandes/activer/reponse"
    processor = SqlAlchemyMessageProcessor(factory)
    assert processor.process(topic, payload) is True
    assert processor.process(topic, payload) is False

    with factory() as session:
        assert session.get(DeviceCommand, command_id).status == "confirmed"
        assert session.get(ChargingSession, session_id).status == "active"
        assert session.get(Reservation, reservation_id).status == "active"


def test_threshold_crossing_records_alert_and_queues_cutoff_once():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    consumer_id = uuid4()
    reservation_id = uuid4()
    session_id = uuid4()
    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        session.add(Consumer(id=consumer_id, display_name="Threshold test"))
        session.add(ChargingPoint(id="A07", name="Borne A07", plug_active=True))
        session.add(
            Reservation(
                id=reservation_id,
                consumer_id=consumer_id,
                charging_point_id="A07",
                starts_at=now - timedelta(minutes=1),
                ends_at=now + timedelta(hours=1),
                status="active",
            )
        )
        session.add(
            ChargingSession(
                id=session_id,
                reservation_id=reservation_id,
                charging_point_id="A07",
                status="active",
                started_at=now - timedelta(seconds=10),
            )
        )
        session.add(
            Threshold(
                charging_point_id="A07",
                resource="ELECTRICITE",
                alert_quantity=4,
                cutoff_quantity=8,
            )
        )

    processor = SqlAlchemyMessageProcessor(factory)
    first_at = now
    processor.process(
        "port/bornes/A07/telemetrie",
        telemetry_payload(uuid4(), reservation_id, session_id, first_at, 0),
    )
    processor.process(
        "port/bornes/A07/telemetrie",
        telemetry_payload(
            uuid4(),
            reservation_id,
            session_id,
            first_at + timedelta(seconds=10),
            9,
        ),
    )
    processor.process(
        "port/bornes/A07/telemetrie",
        telemetry_payload(
            uuid4(),
            reservation_id,
            session_id,
            first_at + timedelta(seconds=20),
            10,
        ),
    )

    with factory() as session:
        events = session.scalars(select(ThresholdEvent)).all()
        commands = session.scalars(
            select(DeviceCommand).where(DeviceCommand.command == "cut_off")
        ).all()
        assert {event.event_type for event in events} == {"alert", "cutoff"}
        assert len(commands) == 1
        assert commands[0].status == "pending"
