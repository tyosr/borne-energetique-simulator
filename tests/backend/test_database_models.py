from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from backend.infrastructure.database.base import Base
from backend.infrastructure.database.models import (
    ChargingPoint,
    Consumer,
    Measurement,
    ProcessedMessage,
)


def test_schema_creates_backend_tables_without_touching_simulator_data():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "consumers",
        "access_tags",
        "charging_points",
        "reservations",
        "charging_sessions",
        "tariffs",
        "tariff_tiers",
        "reservation_tariffs",
        "measurements",
        "processed_messages",
        "thresholds",
        "device_commands",
    } <= table_names


def test_measurements_persist_decimal_and_deduplication_key():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    message_id = uuid4()
    amount = Decimal("1.234567")

    with Session(engine) as session:
        session.add(Consumer(display_name="Test consumer"))
        session.add(ChargingPoint(id="A07", name="Borne A07"))
        session.flush()
        session.add(
            ProcessedMessage(
                message_id=message_id,
                message_type="telemetry",
                payload={"schema_version": "1.0"},
            )
        )
        session.add(
            Measurement(
                message_id=message_id,
                charging_point_id="A07",
                resource="ELECTRICITE",
                unit="kWh",
                reading_type="delta",
                value=amount,
                observed_at=datetime.now(timezone.utc),
                raw_payload={"value": str(amount)},
            )
        )
        session.commit()

        saved = session.query(Measurement).one()
        assert saved.value == amount
