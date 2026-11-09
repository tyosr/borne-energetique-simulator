"""Contrats JSON MQTT versionnés et validation topic/payload."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from backend.domain.entities import Resource

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ResourceReading:
    value: Decimal
    unit: str
    reading_type: str


@dataclass(frozen=True)
class TelemetryMessage:
    message_id: UUID
    charging_point_id: str
    timestamp_utc: datetime
    readings: dict[Resource, ResourceReading]
    plug_active: bool
    session_id: UUID | None
    reservation_id: UUID | None
    payload: dict


@dataclass(frozen=True)
class StateMessage:
    message_id: UUID
    charging_point_id: str
    timestamp_utc: datetime
    status: str
    plug_active: bool
    payload: dict


@dataclass(frozen=True)
class CommandResponse:
    message_id: UUID
    command_id: UUID
    charging_point_id: str
    timestamp_utc: datetime
    command: str
    status: str
    plug_active: bool
    payload: dict


def parse_telemetry(topic: str, raw_payload: str) -> TelemetryMessage:
    payload = _load_payload(raw_payload)
    charging_point_id = _validate_topic(topic, "telemetrie")
    _validate_common(payload, charging_point_id)
    if not isinstance(payload.get("ressources"), dict):
        raise ValueError("Le champ ressources doit être un objet JSON.")
    readings: dict[Resource, ResourceReading] = {}
    for resource_name, values in payload["ressources"].items():
        try:
            resource = Resource(resource_name)
        except ValueError as exc:
            raise ValueError(f"Ressource inconnue dans la télémétrie : {resource_name}.") from exc
        if not isinstance(values, dict):
            raise ValueError(f"La mesure {resource_name} doit être un objet.")
        try:
            value = Decimal(str(values["value"]))
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ValueError(f"La valeur de {resource_name} est invalide.") from exc
        unit = values.get("unit")
        reading_type = values.get("reading_type")
        if not value.is_finite() or value < 0:
            raise ValueError(f"La valeur de {resource_name} doit être finie et positive.")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"L'unité de {resource_name} est obligatoire.")
        if reading_type not in {"delta", "index"}:
            raise ValueError(f"reading_type invalide pour {resource_name}.")
        readings[resource] = ResourceReading(value, unit, reading_type)
    if not readings:
        raise ValueError("La télémétrie doit contenir au moins une ressource.")
    plug_active = payload.get("prise_active")
    if not isinstance(plug_active, bool):
        raise ValueError("prise_active doit être un booléen.")
    return TelemetryMessage(
        message_id=UUID(payload["message_id"]),
        charging_point_id=charging_point_id,
        timestamp_utc=_parse_timestamp(payload["timestamp_utc"]),
        readings=readings,
        plug_active=plug_active,
        session_id=_optional_uuid(payload.get("session_id"), "session_id"),
        reservation_id=_optional_uuid(payload.get("id_reservation"), "id_reservation"),
        payload=payload,
    )


def parse_state(topic: str, raw_payload: str) -> StateMessage:
    payload = _load_payload(raw_payload)
    charging_point_id = _validate_topic(topic, "etat")
    _validate_common(payload, charging_point_id)
    state = payload.get("status")
    if state not in {"online", "offline"}:
        raise ValueError("status doit être online ou offline.")
    plug_active = payload.get("prise_active", False)
    if not isinstance(plug_active, bool):
        raise ValueError("prise_active doit être un booléen.")
    return StateMessage(
        message_id=UUID(payload["message_id"]),
        charging_point_id=charging_point_id,
        timestamp_utc=_parse_timestamp(payload["timestamp_utc"]),
        status=state,
        plug_active=plug_active,
        payload=payload,
    )


def parse_command_response(topic: str, raw_payload: str) -> CommandResponse:
    payload = _load_payload(raw_payload)
    parts = topic.split("/")
    if (
        len(parts) != 6
        or parts[0:2] != ["port", "bornes"]
        or parts[3] != "commandes"
        or parts[5] != "reponse"
    ):
        raise ValueError("Topic de réponse de commande invalide.")
    charging_point_id = parts[2]
    command = {"activer": "activate", "couper": "cut_off"}.get(parts[4])
    if command is None:
        raise ValueError("Topic de commande non pris en charge.")
    _validate_common(payload, charging_point_id)
    if payload.get("command") != command:
        raise ValueError("La commande du topic ne correspond pas au payload.")
    status = payload.get("status")
    if status not in {"confirmed", "rejected"}:
        raise ValueError("La réponse de commande doit être confirmée ou refusée.")
    plug_active = payload.get("prise_active")
    if not isinstance(plug_active, bool):
        raise ValueError("prise_active doit être un booléen.")
    try:
        command_id = UUID(payload["command_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("command_id invalide ou absent.") from exc
    return CommandResponse(
        message_id=UUID(payload["message_id"]),
        command_id=command_id,
        charging_point_id=charging_point_id,
        timestamp_utc=_parse_timestamp(payload["timestamp_utc"]),
        command=command,
        status=status,
        plug_active=plug_active,
        payload=payload,
    )


def _load_payload(raw_payload: str) -> dict:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Payload MQTT JSON invalide.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Le payload MQTT doit être un objet JSON.")
    return payload


def _validate_topic(topic: str, suffix: str) -> str:
    parts = topic.split("/")
    if len(parts) != 4 or parts[:2] != ["port", "bornes"] or parts[3] != suffix:
        raise ValueError(f"Topic MQTT invalide pour {suffix}.")
    if not parts[2].strip():
        raise ValueError("L'identifiant de borne du topic est obligatoire.")
    return parts[2]


def _validate_common(payload: dict, charging_point_id: str) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version doit être {SCHEMA_VERSION}.")
    if payload.get("id_borne") != charging_point_id:
        raise ValueError("id_borne du payload différent de celui du topic.")
    try:
        UUID(payload["message_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("message_id UUID invalide ou absent.") from exc
    _parse_timestamp(payload.get("timestamp_utc"))


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp_utc doit être une date ISO-8601.")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp_utc doit être une date ISO-8601.") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise ValueError("timestamp_utc doit être exprimé en UTC.")
    return timestamp.astimezone(timezone.utc)


def _optional_uuid(value: object, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} doit être un UUID.")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} doit être un UUID.") from exc
