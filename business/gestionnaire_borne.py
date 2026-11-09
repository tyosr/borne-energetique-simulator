"""Cycle de vie d'une borne simulée utilisant uniquement le protocole V2.1."""

from __future__ import annotations

import json
import logging
import random
import threading
from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import UUID, uuid4

log = logging.getLogger(__name__)


class SimulatorCommunication(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def send(
        self, topic: str, message: str, qos: int = 1, retain: bool = False
    ) -> None: ...

    def register_message_handler(
        self, handler: Callable[[str, str], None]
    ) -> None: ...

    def is_connected(self) -> bool: ...


class GestionnaireBorne(threading.Thread):
    def __init__(
        self,
        config: dict,
        communication: SimulatorCommunication,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name=f"borne-{config['id_borne']}", daemon=True)
        self._config = config
        self._communication = communication
        self._stop_event = stop_event
        self._id_borne = config["id_borne"]
        self._resources = tuple(config["ressources"])
        self._interval = int(config["intervalle_releve"])
        self._totals = {resource: 0.0 for resource in self._resources}
        self._session_id: str | None = None
        self._reservation_id: str | None = None
        self._plug_active = False
        self._state_lock = threading.Lock()
        self._processed_commands: dict[str, tuple[str, bool]] = {}
        self.failed = False

    def run(self) -> None:
        try:
            self._communication.register_message_handler(self._handle_message)
            self._communication.connect()
            while (
                not self._communication.is_connected()
                and not self._stop_event.wait(0.1)
            ):
                pass
            if self._stop_event.is_set():
                return
            self._publish_reading()
            while not self._stop_event.wait(self._interval):
                self._publish_reading()
        except Exception:
            self.failed = True
            self._stop_event.set()
            log.exception("[%s] Échec du simulateur.", self._id_borne)
        finally:
            log.info("[%s] Arrêt du simulateur.", self._id_borne)
            self._communication.disconnect()

    def _publish_reading(self) -> None:
        observed_at = datetime.now(timezone.utc)
        with self._state_lock:
            plug_active = self._plug_active
            session_id = self._session_id
            reservation_id = self._reservation_id
            readings = {}
            for resource in self._resources:
                profile = self._config["profil_simulation"][resource]
                amount = (
                    round(
                        random.uniform(float(profile["min"]), float(profile["max"])),
                        3,
                    )
                    if plug_active
                    else 0.0
                )
                self._totals[resource] = round(
                    self._totals[resource] + amount, 3
                )
                readings[resource] = {
                    "value": str(self._totals[resource]),
                    "unit": self._unit_for(resource),
                    "reading_type": "index",
                }

        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "id_borne": self._id_borne,
            "timestamp_utc": observed_at.isoformat(),
            "session_id": session_id,
            "id_reservation": reservation_id,
            "prise_active": plug_active,
            "ressources": readings,
        }
        topic = f"port/bornes/{self._id_borne}/telemetrie"
        self._communication.send(
            topic, json.dumps(payload, separators=(",", ":")), qos=1
        )
        log.info("[%s] Télémétrie publiée : %s", self._id_borne, readings)

    def _handle_message(self, topic: str, raw_payload: str) -> None:
        prefix = f"port/bornes/{self._id_borne}/commandes/"
        if not topic.startswith(prefix):
            log.warning("[%s] Topic MQTT inattendu : %s", self._id_borne, topic)
            return
        command_topic = topic.removeprefix(prefix)
        if command_topic not in {"activer", "couper"}:
            log.warning("[%s] Commande MQTT inconnue : %s", self._id_borne, topic)
            return
        try:
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict):
                raise ValueError("Le payload doit être un objet JSON.")
            command_id = str(UUID(payload["command_id"]))
            command = "activate" if command_topic == "activer" else "cut_off"
            if (
                payload.get("schema_version") != "1.0"
                or payload.get("command") != command
                or payload.get("id_borne") != self._id_borne
            ):
                raise ValueError("Contrat MQTT de commande invalide.")
            session_id = self._optional_uuid(payload.get("session_id"))
            reservation_id = self._optional_uuid(payload.get("id_reservation"))
            if command == "activate" and (session_id is None or reservation_id is None):
                raise ValueError("Une activation exige une session et une réservation.")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            log.warning("[%s] Commande MQTT invalide : %s", self._id_borne, exc)
            return

        with self._state_lock:
            prior = self._processed_commands.get(command_id)
            if prior is not None:
                status, active = prior
            elif command == "activate":
                if self._plug_active and self._session_id != session_id:
                    status, active = "rejected", True
                else:
                    self._session_id = session_id
                    self._reservation_id = reservation_id
                    self._totals = {resource: 0.0 for resource in self._resources}
                    self._plug_active = True
                    status, active = "confirmed", True
                    self._processed_commands[command_id] = (status, active)
            elif (
                self._plug_active
                and session_id is not None
                and session_id != self._session_id
            ):
                status, active = "rejected", True
            else:
                self._plug_active = False
                self._session_id = None
                self._reservation_id = None
                status, active = "confirmed", False
                self._processed_commands[command_id] = (status, active)

        response = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "command_id": command_id,
            "id_borne": self._id_borne,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": status,
            "prise_active": active,
        }
        response_topic = (
            f"port/bornes/{self._id_borne}/commandes/{command_topic}/reponse"
        )
        self._communication.send(
            response_topic,
            json.dumps(response, separators=(",", ":")),
            qos=1,
        )
        self._publish_state(active)

    def _publish_state(self, plug_active: bool) -> None:
        payload = {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "id_borne": self._id_borne,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": "online",
            "prise_active": plug_active,
        }
        self._communication.send(
            f"port/bornes/{self._id_borne}/etat",
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=True,
        )

    @staticmethod
    def _optional_uuid(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Les identifiants de session doivent être des UUID.")
        return str(UUID(value))

    @staticmethod
    def _unit_for(resource: str) -> str:
        return {"ELECTRICITE": "kWh", "EAU": "litres", "GAZ": "m3"}[resource]
