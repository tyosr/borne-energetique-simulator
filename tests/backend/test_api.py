from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.app import create_app
from backend.infrastructure.database.base import Base
from backend.infrastructure.database.models import ChargingPoint


def make_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ChargingPoint(id="A07", name="Borne A07"))
        session.commit()
    return TestClient(create_app(engine=engine))


def test_health_endpoints_and_consumer_creation():
    client = make_client()

    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json()["status"] == "ready"

    response = client.post(
        "/api/v1/consumers",
        json={"display_name": "  Alice  ", "email": "ALICE@example.test"},
    )
    assert response.status_code == 201
    assert response.json()["display_name"] == "Alice"
    assert response.json()["email"] == "alice@example.test"


def test_tag_creation_and_reservation_overlap_validation():
    client = make_client()
    consumer = client.post(
        "/api/v1/consumers", json={"display_name": "Bob"}
    ).json()
    tag_response = client.post(
        "/api/v1/tags",
        json={"consumer_id": consumer["id"], "code": "TAG-001"},
    )
    assert tag_response.status_code == 201

    starts_at = datetime.now(timezone.utc) + timedelta(hours=1)
    ends_at = starts_at + timedelta(hours=2)
    payload = {
        "consumer_id": consumer["id"],
        "charging_point_id": "A07",
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
    }
    first = client.post("/api/v1/reservations", json=payload)
    assert first.status_code == 201
    assert first.json()["status"] == "pending"

    conflict = client.post("/api/v1/reservations", json=payload)
    assert conflict.status_code == 409

    invalid_period = client.post(
        "/api/v1/reservations",
        json={
            **payload,
            "starts_at": ends_at.isoformat(),
            "ends_at": starts_at.isoformat(),
        },
    )
    assert invalid_period.status_code == 422


def test_tariff_assignment_and_cost_estimate():
    client = make_client()
    consumer = client.post(
        "/api/v1/consumers", json={"display_name": "Tariff user"}
    ).json()
    starts_at = datetime.now(timezone.utc) + timedelta(hours=1)
    reservation = client.post(
        "/api/v1/reservations",
        json={
            "consumer_id": consumer["id"],
            "charging_point_id": "A07",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
        },
    ).json()
    tariff = client.post(
        "/api/v1/tariffs",
        json={
            "name": "Tarif énergie",
            "resource": "ELECTRICITE",
            "version": 1,
            "currency": "EUR",
            "tiers": [
                {"lower_bound": "0", "upper_bound": "10", "unit_price": "0.20"},
                {"lower_bound": "10", "upper_bound": None, "unit_price": "0.50"},
            ],
        },
    )
    assert tariff.status_code == 201

    assignment = client.put(
        f"/api/v1/reservations/{reservation['id']}/tariffs",
        json={"resource": "ELECTRICITE", "tariff_id": tariff.json()["id"]},
    )
    assert assignment.status_code == 204

    estimate = client.post(
        f"/api/v1/reservations/{reservation['id']}/cost-estimates",
        json={"resource": "ELECTRICITE", "quantity": "12"},
    )
    assert estimate.status_code == 200
    assert Decimal(estimate.json()["amount"]) == Decimal("3")
    assert len(estimate.json()["breakdown"]) == 2


def test_tag_for_unknown_consumer_is_rejected():
    client = make_client()
    response = client.post(
        "/api/v1/tags",
        json={"consumer_id": str(uuid4()), "code": "UNKNOWN-CONSUMER"},
    )
    assert response.status_code == 404


def test_threshold_order_and_station_state_endpoints():
    client = make_client()
    thresholds = client.put(
        "/api/v1/stations/A07/thresholds/ELECTRICITE",
        json={"alert_quantity": "5", "cutoff_quantity": "10", "cutoff_cost": "25"},
    )
    assert thresholds.status_code == 200
    assert thresholds.json()["cutoff_quantity"] == "10"
    assert client.get("/api/v1/stations/A07/thresholds").json()[0]["resource"] == "ELECTRICITE"
    assert client.get("/api/v1/stations/A07/state").json()["is_online"] is False

    invalid = client.put(
        "/api/v1/stations/A07/thresholds/ELECTRICITE",
        json={"alert_quantity": "10", "cutoff_quantity": "5"},
    )
    assert invalid.status_code == 422

    missing_station = client.put(
        "/api/v1/stations/MISSING/thresholds/EAU",
        json={"cutoff_quantity": "100"},
    )
    assert missing_station.status_code == 404


def test_simulated_tag_activation_is_queued_until_mqtt_confirmation():
    client = make_client()
    consumer = client.post(
        "/api/v1/consumers", json={"display_name": "Activatable"}
    ).json()
    tag = client.post(
        "/api/v1/tags",
        json={"consumer_id": consumer["id"], "code": "TAG-ACTIVE"},
    )
    assert tag.status_code == 201
    now = datetime.now(timezone.utc)
    reservation = client.post(
        "/api/v1/reservations",
        json={
            "consumer_id": consumer["id"],
            "charging_point_id": "A07",
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert reservation.status_code == 201

    activation = client.post(
        "/api/v1/stations/A07/activate", json={"tag_code": "TAG-ACTIVE"}
    )
    assert activation.status_code == 202
    command = activation.json()
    assert command["status"] == "pending_activation"
    assert client.get(f"/api/v1/commands/{command['command_id']}").json()["status"] == "pending"
    assert client.get(f"/api/v1/sessions/{command['session_id']}").json()["status"] == "pending_activation"

    duplicate_activation = client.post(
        "/api/v1/stations/A07/activate", json={"tag_code": "TAG-ACTIVE"}
    )
    assert duplicate_activation.status_code == 409


def test_disabled_tag_is_denied_activation():
    client = make_client()
    consumer = client.post(
        "/api/v1/consumers", json={"display_name": "Disabled tag"}
    ).json()
    tag = client.post(
        "/api/v1/tags",
        json={"consumer_id": consumer["id"], "code": "TAG-DISABLED"},
    )
    assert tag.status_code == 201
    update = client.patch(
        "/api/v1/tags/TAG-DISABLED", json={"status": "disabled"}
    )
    assert update.status_code == 200
    denied = client.post(
        "/api/v1/stations/A07/activate", json={"tag_code": "TAG-DISABLED"}
    )
    assert denied.status_code == 403
