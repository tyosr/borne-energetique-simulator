import threading
import json
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from paho.mqtt import client as mqtt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.infrastructure.database.base import Base
from backend.infrastructure.database.models import (
    ChargingPoint,
    ChargingSession,
    Consumer,
    DeviceCommand,
    Measurement,
    ProcessedMessage,
    Reservation,
)
from backend.settings import Settings
from backend.worker.mqtt_worker import MqttWorker


@pytest.mark.integration
def test_worker_receives_and_persists_telemetry_over_real_mqtt(tmp_path):
    host = os.getenv("MQTT_TEST_HOST")
    port = os.getenv("MQTT_TEST_PORT")
    if not host or not port:
        pytest.skip("Définir MQTT_TEST_HOST et MQTT_TEST_PORT pour le test broker réel.")

    database_path = (tmp_path / "mqtt-test.db").as_posix()
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    consumer_id = uuid4()
    reservation_id = uuid4()
    session_id = uuid4()
    command_id = uuid4()
    with factory.begin() as session:
        session.add(Consumer(id=consumer_id, display_name="MQTT integration"))
        session.add(ChargingPoint(id="TEST-MQTT", name="Borne MQTT"))
        session.add(
            Reservation(
                id=reservation_id,
                consumer_id=consumer_id,
                charging_point_id="TEST-MQTT",
                starts_at=now - timedelta(minutes=1),
                ends_at=now + timedelta(hours=1),
                status="pending",
            )
        )
        session.add(
            ChargingSession(
                id=session_id,
                reservation_id=reservation_id,
                charging_point_id="TEST-MQTT",
                status="pending_activation",
            )
        )
        session.add(
            DeviceCommand(
                command_id=command_id,
                charging_point_id="TEST-MQTT",
                session_id=session_id,
                command="activate",
                status="pending",
                payload={
                    "session_id": str(session_id),
                    "id_reservation": str(reservation_id),
                },
                expires_at=now + timedelta(seconds=20),
            )
        )
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        mqtt_host=host,
        mqtt_port=int(port),
        log_level="INFO",
    )
    worker = MqttWorker(settings, factory)
    worker_thread = threading.Thread(target=worker.start, daemon=True)
    publisher = mqtt.Client(
        client_id=f"mqtt-test-publisher-{uuid4()}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    connected = threading.Event()
    subscribed = threading.Event()
    received_command = threading.Event()
    command_payloads: list[str] = []

    def on_message(
        client: mqtt.Client, userdata: list[str], message: mqtt.MQTTMessage
    ) -> None:
        if client.is_connected():
            userdata.append(message.payload.decode("utf-8"))
            received_command.set()

    publisher.on_connect = lambda *_: connected.set()
    publisher.on_subscribe = lambda *_: subscribed.set()
    publisher.user_data_set(command_payloads)
    publisher.on_message = on_message
    try:
        publisher.connect(host, int(port), keepalive=30)
        publisher.loop_start()
        assert connected.wait(10), "Le publisher de test n'a pas rejoint le broker."
        publisher.subscribe("port/bornes/TEST-MQTT/commandes/activer", qos=1)
        assert subscribed.wait(10), "Le publisher n'a pas souscrit au topic de commande."

        worker_thread.start()
        deadline = time.monotonic() + 10
        while not worker._client.is_connected() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert worker._client.is_connected(), "Le worker n'a pas rejoint le broker."
        assert received_command.wait(10), "L'outbox n'a pas publié la commande MQTT."

        published_command = json.loads(command_payloads[0])
        assert published_command["command_id"] == str(command_id)
        assert published_command["command"] == "activate"

        response_id = uuid4()
        response_payload = {
            "schema_version": "1.0",
            "message_id": str(response_id),
            "command_id": str(command_id),
            "id_borne": "TEST-MQTT",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "command": "activate",
            "status": "confirmed",
            "prise_active": True,
        }
        result = publisher.publish(
            "port/bornes/TEST-MQTT/commandes/activer/reponse",
            json.dumps(response_payload),
            qos=1,
        )
        result.wait_for_publish(timeout=10)
        assert result.is_published(), "Le broker n'a pas confirmé la réponse."

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with factory() as session:
                command = session.get(DeviceCommand, command_id)
                charging_session = session.get(ChargingSession, session_id)
            if (
                command is not None
                and charging_session is not None
                and command.status == "confirmed"
                and charging_session.status == "active"
            ):
                break
            time.sleep(0.05)
        assert command is not None
        assert charging_session is not None
        assert command.status == "confirmed"
        assert charging_session.status == "active"

        message_id = uuid4()
        payload = {
            "schema_version": "1.0",
            "message_id": str(message_id),
            "id_borne": "TEST-MQTT",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "session_id": str(session_id),
            "id_reservation": str(reservation_id),
            "prise_active": True,
            "ressources": {
                "ELECTRICITE": {
                    "value": "2.5",
                    "unit": "kWh",
                    "reading_type": "index",
                }
            },
        }
        result = publisher.publish(
            "port/bornes/TEST-MQTT/telemetrie",
            json.dumps(payload),
            qos=1,
        )
        result.wait_for_publish(timeout=10)
        assert result.is_published(), "Le broker n'a pas confirmé la publication."

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with factory() as session:
                processed = session.get(ProcessedMessage, message_id)
                delta_rows = session.scalars(
                    select(Measurement).where(
                        Measurement.session_id == session_id,
                        Measurement.reading_type == "delta",
                    )
                ).all()
                processed_count = len(
                    session.scalars(
                        select(ProcessedMessage).where(
                            ProcessedMessage.message_id.in_(
                                (response_id, message_id)
                            )
                        )
                    ).all()
                )
            if processed is not None and len(delta_rows) == 1:
                break
            time.sleep(0.05)
        assert processed is not None, "Le worker n'a pas persisté le message MQTT."
        assert len(delta_rows) == 1
        assert delta_rows[0].value == 2.5
        assert processed_count == 2
    finally:
        worker.stop()
        if worker_thread.is_alive():
            worker_thread.join(timeout=10)
        if publisher.is_connected():
            publisher.disconnect()
        publisher.loop_stop()
        engine.dispose()
