import json
import threading
import uuid

from business.gestionnaire_borne import GestionnaireBorne, SimulatorCommunication


class FakeCommunication(SimulatorCommunication):
    def __init__(self):
        self.messages = []
        self.handler = None

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def send(
        self, topic: str, message: str, qos: int = 1, retain: bool = False
    ) -> None:
        self.messages.append((topic, json.loads(message), qos, retain))

    def register_message_handler(self, handler) -> None:
        self.handler = handler

    def is_connected(self) -> bool:
        return True


def make_manager():
    communication = FakeCommunication()
    config = {
        "id_borne": "A07",
        "intervalle_releve": 8,
        "ressources": ["ELECTRICITE", "EAU"],
        "profil_simulation": {
            "ELECTRICITE": {"min": 1, "max": 2},
            "EAU": {"min": 2, "max": 3},
        },
    }
    manager = GestionnaireBorne(config, communication, threading.Event())
    communication.register_message_handler(manager._handle_message)
    return manager, communication


def make_activation(command_id, session_id, reservation_id):
    return {
        "schema_version": "1.0",
        "message_id": str(uuid.uuid4()),
        "command_id": str(command_id),
        "id_borne": "A07",
        "command": "activate",
        "session_id": str(session_id),
        "id_reservation": str(reservation_id),
    }


def test_simulator_publishes_versioned_index_and_waits_for_activation():
    manager, communication = make_manager()
    manager._publish_reading()
    first_topic, first, _, _ = communication.messages[-1]

    assert first_topic == "port/bornes/A07/telemetrie"
    assert first["schema_version"] == "1.0"
    assert first["prise_active"] is False
    assert first["session_id"] is None
    assert first["ressources"]["ELECTRICITE"] == {
        "value": "0.0",
        "unit": "kWh",
        "reading_type": "index",
    }

    session_id = uuid.uuid4()
    reservation_id = uuid.uuid4()
    command_id = uuid.uuid4()
    activation = make_activation(command_id, session_id, reservation_id)
    communication.handler(
        "port/bornes/A07/commandes/activer", json.dumps(activation)
    )
    response_topic, response, _, _ = communication.messages[-2]
    assert response_topic == "port/bornes/A07/commandes/activer/reponse"
    assert response["status"] == "confirmed"
    assert response["command_id"] == str(command_id)

    manager._publish_reading()
    _, active_reading, _, _ = communication.messages[-1]
    assert active_reading["session_id"] == str(session_id)
    assert active_reading["id_reservation"] == str(reservation_id)
    first_index = float(active_reading["ressources"]["ELECTRICITE"]["value"])
    assert first_index >= 1

    communication.handler(
        "port/bornes/A07/commandes/activer", json.dumps(activation)
    )
    manager._publish_reading()
    _, repeated_reading, _, _ = communication.messages[-1]
    assert float(repeated_reading["ressources"]["ELECTRICITE"]["value"]) > first_index


def test_simulator_confirms_cutoff_and_stops_session_consumption():
    manager, communication = make_manager()
    session_id = uuid.uuid4()
    reservation_id = uuid.uuid4()
    communication.handler(
        "port/bornes/A07/commandes/activer",
        json.dumps(make_activation(uuid.uuid4(), session_id, reservation_id)),
    )
    manager._publish_reading()
    active_index = communication.messages[-1][1]["ressources"]["ELECTRICITE"]["value"]

    command_id = uuid.uuid4()
    cutoff = {
        **make_activation(command_id, session_id, reservation_id),
        "command": "cut_off",
    }
    communication.handler(
        "port/bornes/A07/commandes/couper", json.dumps(cutoff)
    )
    response_topic, response, _, _ = communication.messages[-2]
    assert response_topic == "port/bornes/A07/commandes/couper/reponse"
    assert response["status"] == "confirmed"
    assert response["prise_active"] is False

    manager._publish_reading()
    _, inactive_reading, _, _ = communication.messages[-1]
    assert inactive_reading["prise_active"] is False
    assert inactive_reading["session_id"] is None
    assert inactive_reading["ressources"]["ELECTRICITE"]["value"] == active_index


def test_simulator_ignores_commands_for_another_station():
    manager, communication = make_manager()
    payload = make_activation(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    communication.handler(
        "port/bornes/B12/commandes/activer", json.dumps(payload)
    )

    assert manager._plug_active is False
    assert communication.messages == []
