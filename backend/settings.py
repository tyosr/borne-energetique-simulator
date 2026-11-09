"""Configuration externe du backend, indépendante du simulateur."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str
    mqtt_host: str
    mqtt_port: int
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://borne:borne_dev@localhost:5432/borne_v21",
        ).strip()
        mqtt_host = os.environ.get("MQTT_HOST", "localhost").strip()
        raw_mqtt_port = os.environ.get("MQTT_PORT", "1883").strip()
        log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()

        parsed_database_url = urlsplit(database_url)
        if parsed_database_url.scheme not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("DATABASE_URL doit désigner une base PostgreSQL.")
        if not parsed_database_url.hostname or not parsed_database_url.path.strip("/"):
            raise ValueError("DATABASE_URL doit inclure un hôte et un nom de base.")
        if not mqtt_host:
            raise ValueError("MQTT_HOST ne peut pas être vide.")

        try:
            mqtt_port = int(raw_mqtt_port)
        except ValueError as exc:
            raise ValueError("MQTT_PORT doit être un entier entre 1 et 65535.") from exc
        if not 1 <= mqtt_port <= 65535:
            raise ValueError("MQTT_PORT doit être un entier entre 1 et 65535.")
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(
                "LOG_LEVEL doit être CRITICAL, ERROR, WARNING, INFO ou DEBUG."
            )

        return cls(
            database_url=database_url,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            log_level=log_level,
        )
