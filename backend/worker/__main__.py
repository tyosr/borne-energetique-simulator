"""Entrée du processus worker MQTT."""

from __future__ import annotations

import logging

from backend.infrastructure.database.engine import (
    create_database_engine,
    create_session_factory,
)
from backend.settings import Settings
from backend.worker.mqtt_worker import MqttWorker


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    engine = create_database_engine(settings)
    worker = MqttWorker(settings, create_session_factory(engine))
    try:
        worker.start()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Arrêt demandé pour le worker MQTT.")
    finally:
        worker.stop()
        engine.dispose()


if __name__ == "__main__":
    main()
