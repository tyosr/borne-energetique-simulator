"""Client MQTT backend et boucle de publication de l'outbox."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from paho.mqtt import client as mqtt
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend.domain.messages import SCHEMA_VERSION
from backend.infrastructure.mqtt.processor import CommandOutbox, SqlAlchemyMessageProcessor
from backend.settings import Settings

log = logging.getLogger(__name__)


class MqttWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._processor = SqlAlchemyMessageProcessor(session_factory)
        self._outbox = CommandOutbox(session_factory)
        self._stop = threading.Event()
        self._client = client or mqtt.Client(
            client_id="borne-backend-worker",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def start(self) -> None:
        self._client.connect_async(
            self._settings.mqtt_host, self._settings.mqtt_port, keepalive=60
        )
        self._client.loop_start()
        log.info(
            "Worker MQTT démarré (broker %s:%d).",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
        )
        try:
            while not self._stop.wait(0.5):
                if self._client.is_connected():
                    self._publish_outbox()
        finally:
            if self._client.is_connected():
                self._client.disconnect()
            self._client.loop_stop()
            log.info("Worker MQTT arrêté.")

    def stop(self) -> None:
        self._stop.set()

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any = None,
    ) -> None:
        if reason_code.is_failure:
            log.error("Connexion au broker MQTT refusée: %s", reason_code)
            return
        client.subscribe(
            [
                ("port/bornes/+/telemetrie", 1),
                ("port/bornes/+/etat", 1),
                ("port/bornes/+/commandes/+/reponse", 1),
            ]
        )
        log.info("Worker connecté au broker et abonnements enregistrés.")

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _disconnect_flags: Any,
        reason_code: Any,
        _properties: Any = None,
    ) -> None:
        if reason_code.is_failure:
            log.warning("Connexion MQTT perdue (%s); Paho tentera une reconnexion.", reason_code)
        else:
            log.info("Déconnexion MQTT propre.")

    def _on_message(
        self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        try:
            processed = self._processor.process(message.topic, payload)
        except ValueError as exc:
            log.warning("Message MQTT invalide sur %s: %s", message.topic, exc)
        except SQLAlchemyError:
            log.exception("Échec de persistance du message MQTT sur %s.", message.topic)
        else:
            if processed:
                log.info("Message MQTT traité: %s", message.topic)
            else:
                log.info("Message MQTT dupliqué ignoré: %s", message.topic)

    def _publish_outbox(self) -> None:
        try:
            commands = self._outbox.pending()
            for command in commands:
                verb = "activer" if command.command == "activate" else "couper"
                topic = (
                    f"port/bornes/{command.charging_point_id}/commandes/{verb}"
                )
                payload = {
                    **command.payload,
                    "schema_version": SCHEMA_VERSION,
                    "message_id": str(uuid4()),
                    "command_id": str(command.command_id),
                    "id_borne": command.charging_point_id,
                    "command": command.command,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
                info = self._client.publish(
                    topic, json.dumps(payload, separators=(",", ":")), qos=1
                )
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    log.warning(
                        "Publication de commande échouée (%s) pour %s.",
                        info.rc,
                        command.command_id,
                    )
                    continue
                try:
                    info.wait_for_publish(timeout=5)
                except RuntimeError:
                    log.warning("Confirmation de publication absente pour %s.", command.command_id)
                    continue
                if not info.is_published():
                    log.warning("Publication MQTT non confirmée pour %s.", command.command_id)
                    continue
                self._outbox.mark_sent(command.command_id)
        except SQLAlchemyError:
            log.exception("Échec de lecture ou mise à jour de l'outbox MQTT.")
