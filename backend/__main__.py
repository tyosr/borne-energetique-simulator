"""Validation de configuration du backend."""

from __future__ import annotations

import argparse
import logging

from backend.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Backend du simulateur de bornes.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="valider la configuration du backend sans démarrer les services",
    )
    args = parser.parse_args()
    if not args.check:
        parser.error(
            "utilisez --check, python -m backend.api ou python -m backend.worker"
        )

    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("borne_backend").info(
        "Configuration valide (MQTT %s:%d, base PostgreSQL configurée)",
        settings.mqtt_host,
        settings.mqtt_port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
