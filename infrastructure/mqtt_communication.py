"""Client MQTT V2.1 de la borne simulée."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from paho.mqtt import client as mqtt

log = logging.getLogger(__name__)


class MqttCommunication:
    def __init__(
        self,
        id_borne: str,
        hote: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._id_borne = id_borne
        self._hote = hote
        self._port = port
        self._handler: Callable[[str, str], None] | None = None
        self._client = mqtt.Client(client_id=f"borne_{id_borne}", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self._connected = False

        if username is not None:
            self._client.username_pw_set(username, password)

        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.enable_logger(logger=log)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        topic_etat = f"port/bornes/{id_borne}/etat"
        self._client.will_set(topic_etat,
                              payload=json.dumps(self._etat_payload("offline"), ensure_ascii=False),
                              qos=1,
                              retain=True)

    def connect(self) -> None:
        self._client.connect_async(self._hote, self._port, keepalive=60)
        self._client.loop_start()
        log.info(
            "[%s] Connexion MQTT lancée vers %s:%s",
            self._id_borne,
            self._hote,
            self._port,
        )

    def disconnect(self) -> None:
        """Déconnecte proprement et publie l'état offline retained."""
        try:
            if self._client.is_connected():
                self._client.publish(
                    f"port/bornes/{self._id_borne}/etat",
                    json.dumps(self._etat_payload("offline"), ensure_ascii=False),
                    qos=1,
                    retain=True,
                )
                self._client.disconnect()
        except Exception as exc:  # pragma: no cover - dépend du runtime réseau
            log.warning("[%s] Déconnexion MQTT non propre : %s", self._id_borne, exc)
        finally:
            self._connected = False
            self._client.loop_stop()

    def send(self, topic: str, message: str, qos: int = 1, retain: bool = False) -> None:
        """Publie un message MQTT sur un topic donné."""
        if not self._client.is_connected():
            log.warning("[%s] Tentative d'envoi sans connexion MQTT sur %s", self._id_borne, topic)
            return
        self._client.publish(topic, message, qos=qos, retain=retain)

    def register_message_handler(self, handler: Callable[[str, str], None]) -> None:
        self._handler = handler

    def is_connected(self) -> bool:
        return self._connected or self._client.is_connected()

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata,
        _flags,
        rc,
        _properties=None,
    ) -> None:
        if rc == 0:
            self._connected = True
            self._client.subscribe(f"port/bornes/{self._id_borne}/commandes/+", qos=1)
            self._client.publish(
                f"port/bornes/{self._id_borne}/etat",
                json.dumps(self._etat_payload("online"), ensure_ascii=False),
                qos=1,
                retain=True,
            )
            log.info("[%s] Connecté à MQTT. Abonnements enregistrés.", self._id_borne)
        else:
            self._connected = False
            log.warning("[%s] Échec de connexion MQTT (rc=%s)", self._id_borne, rc)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata,
        _disconnect_flags,
        rc,
        _properties=None,
    ) -> None:
        self._connected = False
        if rc != 0:
            log.warning("[%s] Déconnecté de MQTT (rc=%s) : tentative de reconnexion automatique.", self._id_borne, rc)
        else:
            log.info("[%s] Déconnexion propre MQTT.", self._id_borne)

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata,
        msg: mqtt.MQTTMessage,
    ) -> None:
        if self._handler is None:
            return
        payload = msg.payload.decode("utf-8", errors="replace") if msg.payload else ""
        self._handler(msg.topic, payload)

    def _etat_payload(self, status: str) -> dict[str, str]:
        return {
            "schema_version": "1.0",
            "message_id": str(uuid4()),
            "id_borne": self._id_borne,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": status,
        }
