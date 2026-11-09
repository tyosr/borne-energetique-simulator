#!/usr/bin/env python3
"""Démarre les simulateurs V2.1 déclarés dans la configuration."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from pathlib import Path

from business.gestionnaire_borne import GestionnaireBorne
from infrastructure.mqtt_communication import MqttCommunication

ROOT = Path(__file__).resolve().parent
RESOURCES = {"ELECTRICITE", "EAU", "GAZ"}
log = logging.getLogger("BorneSimulator")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Fichier de configuration introuvable : {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Fichier JSON invalide ({path}) : {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"La configuration doit être un objet JSON : {path}")
    return value


def positive_int(value: object, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} doit être un entier positif.") from exc
    if parsed <= 0 or str(parsed) != str(value).strip():
        raise ValueError(f"{label} doit être un entier positif.")
    return parsed


def prepare_station(
    station_id: str,
    broker_host: str,
    broker_port: int,
) -> tuple[dict, MqttCommunication]:
    config_path = ROOT / "config" / "bornes" / f"borne_{station_id}.json"
    config = load_json(config_path)
    if config.get("id_borne") != station_id:
        raise ValueError(f"id_borne incohérent dans {config_path}.")

    resources = config.get("ressources")
    profiles = config.get("profil_simulation")
    if (
        not isinstance(resources, list)
        or not resources
        or any(
            not isinstance(resource, str) or resource not in RESOURCES
            for resource in resources
        )
    ):
        raise ValueError(f"Liste de ressources invalide dans {config_path}.")
    if not isinstance(profiles, dict):
        raise ValueError(f"profil_simulation invalide dans {config_path}.")
    for resource in resources:
        profile = profiles.get(resource)
        if not isinstance(profile, dict):
            raise ValueError(f"Profil manquant pour {resource} dans {config_path}.")
        minimum = profile.get("min")
        maximum = profile.get("max")
        if (
            isinstance(minimum, bool)
            or isinstance(maximum, bool)
            or not isinstance(minimum, (int, float))
            or not isinstance(maximum, (int, float))
            or not math.isfinite(minimum)
            or not math.isfinite(maximum)
            or minimum < 0
            or maximum < minimum
        ):
            raise ValueError(f"Plage de simulation invalide pour {resource}.")

    config["intervalle_releve"] = positive_int(
        os.environ.get("BORNE_INTERVALLE_RELEVE", config.get("intervalle_releve")),
        "BORNE_INTERVALLE_RELEVE",
    )
    credentials = config.get("identifiants", {})
    if not isinstance(credentials, dict):
        raise ValueError(f"identifiants invalide dans {config_path}.")
    username = os.environ.get("MQTT_USERNAME") or credentials.get("username") or None
    password = os.environ.get("MQTT_PASSWORD") or credentials.get("password") or None

    communication = MqttCommunication(
        station_id,
        hote=os.environ.get("MQTT_HOST") or config.get("hote") or broker_host,
        port=positive_int(
            os.environ.get("MQTT_PORT") or config.get("port") or broker_port,
            "MQTT_PORT",
        ),
        username=username,
        password=password,
    )
    return config, communication


def main() -> int:
    global_config_path = ROOT / "config" / "simulator.json"
    try:
        global_config = load_json(global_config_path)
        count = positive_int(global_config.get("nombre_bornes"), "nombre_bornes")
        station_ids = global_config.get("bornes")
        broker = global_config.get("broker")
        if not isinstance(station_ids, list) or not isinstance(broker, dict):
            raise ValueError("La configuration globale des bornes ou du broker est invalide.")
        if count > 5 or len(station_ids) < count:
            raise ValueError("Il faut configurer entre 1 et 5 bornes.")
        if (
            any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", item)
                for item in station_ids)
            or len(set(station_ids)) != len(station_ids)
        ):
            raise ValueError("Les identifiants des bornes sont invalides ou dupliqués.")
        broker_host = os.environ.get("MQTT_HOST") or broker.get("hote")
        if not isinstance(broker_host, str) or not broker_host.strip():
            raise ValueError("MQTT_HOST ou broker.hote doit être défini.")
        broker_port = positive_int(
            os.environ.get("MQTT_PORT") or broker.get("port", 1883),
            "MQTT_PORT",
        )
        log_level = str(global_config.get("niveau_log", "INFO")).upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("niveau_log n'est pas un niveau de journalisation valide.")
    except ValueError as exc:
        logging.basicConfig(level=logging.INFO)
        log.error("Démarrage impossible : %s", exc)
        return 1

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )
    selected_id = os.environ.get("BORNE_ID")
    if selected_id is not None and selected_id not in station_ids:
        log.error("BORNE_ID=%s n'est pas déclaré dans la configuration.", selected_id)
        return 1
    selected_ids = [selected_id] if selected_id else station_ids[:count]

    try:
        stations = [
            prepare_station(station_id, broker_host, broker_port)
            for station_id in selected_ids
        ]
    except ValueError as exc:
        log.error("Démarrage impossible : %s", exc)
        return 1

    stop_event = threading.Event()
    workers = [
        GestionnaireBorne(config, communication, stop_event)
        for config, communication in stations
    ]
    for worker in workers:
        worker.start()
        log.info("[%s] Simulateur V2.1 démarré.", worker.name.removeprefix("borne-"))

    try:
        while any(worker.is_alive() for worker in workers):
            for worker in workers:
                worker.join(timeout=0.5)
    except KeyboardInterrupt:
        log.info("Arrêt demandé.")
    finally:
        stop_event.set()
        for worker in workers:
            worker.join(timeout=5)
    return int(any(worker.failed for worker in workers))


if __name__ == "__main__":
    raise SystemExit(main())
