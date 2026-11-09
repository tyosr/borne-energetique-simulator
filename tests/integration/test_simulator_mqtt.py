import json
import os
import threading
from uuid import uuid4

import pytest
from paho.mqtt import client as mqtt

from business.gestionnaire_borne import GestionnaireBorne
from infrastructure.mqtt_communication import MqttCommunication


@pytest.mark.integration
def test_simulator_publishes_v21_telemetry_and_disconnects_cleanly():
    host = os.getenv("MQTT_TEST_HOST")
    port = os.getenv("MQTT_TEST_PORT")
    if not host or not port:
        pytest.skip("Définir MQTT_TEST_HOST et MQTT_TEST_PORT pour le test broker réel.")

    station_id = f"TEST-{uuid4().hex[:8]}"
    topic = f"port/bornes/{station_id}/telemetrie"
    observer_connected = threading.Event()
    subscribed = threading.Event()
    telemetry_received = threading.Event()
    received_payloads: list[dict] = []

    def on_message(
        _client: mqtt.Client, _userdata: object, message: mqtt.MQTTMessage
    ) -> None:
        received_payloads.append(json.loads(message.payload.decode("utf-8")))
        telemetry_received.set()

    observer = mqtt.Client(
        client_id=f"simulator-test-observer-{uuid4()}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    observer.on_connect = lambda *_: observer_connected.set()
    observer.on_subscribe = lambda *_: subscribed.set()
    observer.on_message = on_message
    observer.connect(host, int(port), keepalive=30)
    observer.loop_start()

    stop_event = threading.Event()
    communication = MqttCommunication(station_id, host, int(port))
    manager = GestionnaireBorne(
        {
            "id_borne": station_id,
            "intervalle_releve": 1,
            "ressources": ["ELECTRICITE"],
            "profil_simulation": {
                "ELECTRICITE": {"min": 1.0, "max": 2.0},
            },
        },
        communication,
        stop_event,
    )
    try:
        assert observer_connected.wait(10), "Le client observateur ne s'est pas connecté."
        observer.subscribe(topic, qos=1)
        assert subscribed.wait(10), "L'abonnement du client observateur a échoué."

        manager.start()
        assert telemetry_received.wait(10), "Le simulateur n'a pas publié sa télémétrie."
        payload = received_payloads[0]
        assert payload["schema_version"] == "1.0"
        assert payload["id_borne"] == station_id
        assert payload["prise_active"] is False
        assert payload["ressources"]["ELECTRICITE"]["reading_type"] == "index"
    finally:
        stop_event.set()
        if manager.ident is not None:
            manager.join(timeout=5)
        if observer.is_connected():
            observer.disconnect()
        observer.loop_stop()

    assert not manager.is_alive(), "Le simulateur ne s'est pas arrêté proprement."
    assert not manager.failed
