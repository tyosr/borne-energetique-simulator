import pytest

from backend.settings import Settings


def test_settings_use_local_development_defaults(monkeypatch):
    for name in ("DATABASE_URL", "MQTT_HOST", "MQTT_PORT", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.mqtt_host == "localhost"
    assert settings.mqtt_port == 1883
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_settings_reject_invalid_mqtt_ports(monkeypatch, value):
    monkeypatch.setenv("MQTT_PORT", value)

    with pytest.raises(ValueError, match="MQTT_PORT"):
        Settings.from_env()
