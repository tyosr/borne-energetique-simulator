"""Cas d'utilisation métier, exprimés uniquement via les ports du domaine."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from backend.domain.entities import (
    ActivationRequest,
    AccessTag,
    ChargingSessionState,
    ChargingPointState,
    Consumer,
    ConsumptionTotal,
    CostEstimate,
    DeviceCommandStatus,
    Measurement,
    Reservation,
    Resource,
    Tariff,
    TariffTier,
    ThresholdEventRecord,
    ThresholdConfig,
)
from backend.domain.ports import (
    AccessTagPort,
    ConsumerPort,
    ReadModelPort,
    ReservationPort,
    ActivationPort,
    TariffPort,
    ThresholdPort,
)
from backend.domain.tariffs import calculate_progressive_cost


class ConsumerService:
    def __init__(self, consumers: ConsumerPort) -> None:
        self._consumers = consumers

    def create(self, display_name: str, email: str | None = None) -> Consumer:
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("Le nom du consommateur est obligatoire.")
        normalized_email = email.strip().lower() if email else None
        return self._consumers.create_consumer(normalized_name, normalized_email)


class AccessTagService:
    def __init__(self, consumers: ConsumerPort, tags: AccessTagPort) -> None:
        self._consumers = consumers
        self._tags = tags

    def create(
        self, consumer_id: UUID, code: str, expires_at: datetime | None = None
    ) -> AccessTag:
        normalized_code = code.strip()
        if not normalized_code:
            raise ValueError("Le code du tag est obligatoire.")
        if expires_at is not None and expires_at.tzinfo is None:
            raise ValueError("La date d'expiration du tag doit inclure un fuseau horaire.")
        if not self._consumers.consumer_exists(consumer_id):
            raise LookupError("Consommateur introuvable.")
        return self._tags.create_tag(consumer_id, normalized_code, expires_at)

    def authorize(self, code: str, now: datetime | None = None) -> AccessTag:
        tag = self._tags.find_by_code(code.strip())
        if tag is None:
            raise LookupError("Tag inconnu.")
        current_time = now or datetime.now(timezone.utc)
        if tag.status != "active":
            raise PermissionError("Le tag est désactivé.")
        expires_at = tag.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= current_time:
                raise PermissionError("Le tag a expiré.")
        return tag

    def set_status(self, code: str, status: str) -> AccessTag:
        if status not in {"active", "disabled"}:
            raise ValueError("Le statut du tag doit être active ou disabled.")
        return self._tags.set_status(code.strip(), status)


class ActivationService:
    def __init__(
        self,
        tags: AccessTagService,
        activations: ActivationPort,
    ) -> None:
        self._tags = tags
        self._activations = activations

    def request(self, code: str, charging_point_id: str) -> ActivationRequest:
        tag = self._tags.authorize(code)
        return self._activations.activate(
            tag, charging_point_id, datetime.now(timezone.utc)
        )

    def command_status(self, command_id: UUID) -> DeviceCommandStatus:
        command = self._activations.get_command(command_id)
        if command is None:
            raise LookupError("Commande introuvable.")
        return command

    def session_status(self, session_id: UUID) -> ChargingSessionState:
        charging_session = self._activations.get_session(session_id)
        if charging_session is None:
            raise LookupError("Session introuvable.")
        return charging_session


class ReservationService:
    def __init__(self, reservations: ReservationPort) -> None:
        self._reservations = reservations

    def create(
        self,
        consumer_id: UUID,
        charging_point_id: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Reservation:
        if starts_at.tzinfo is None or ends_at.tzinfo is None:
            raise ValueError("Les dates de réservation doivent inclure un fuseau horaire.")
        if ends_at <= starts_at:
            raise ValueError("La fin de réservation doit être postérieure au début.")
        if not charging_point_id.strip():
            raise ValueError("L'identifiant de la borne est obligatoire.")
        return self._reservations.create_if_available(
            consumer_id,
            charging_point_id.strip(),
            starts_at.astimezone(timezone.utc),
            ends_at.astimezone(timezone.utc),
        )

    def list_for_point(
        self, charging_point_id: str, offset: int = 0, limit: int = 50
    ) -> list[Reservation]:
        if offset < 0:
            raise ValueError("offset doit être positif ou nul.")
        if not 1 <= limit <= 100:
            raise ValueError("limit doit être compris entre 1 et 100.")
        return self._reservations.list_for_point(charging_point_id, offset, limit)


class TariffService:
    def __init__(self, tariffs: TariffPort) -> None:
        self._tariffs = tariffs

    def estimate(
        self, reservation_id: UUID, resource: str, quantity: Decimal | str | int
    ) -> CostEstimate:
        try:
            amount = Decimal(str(quantity))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("La quantité doit être un nombre décimal valide.") from exc
        if not amount.is_finite():
            raise ValueError("La quantité doit être un nombre fini.")
        try:
            normalized_resource = Resource(resource).value
        except ValueError as exc:
            raise ValueError("Ressource inconnue.") from exc
        tariff = self._tariffs.get_for_reservation(
            reservation_id, normalized_resource
        )
        if tariff is None:
            raise LookupError("Aucun tarif n'est associé à cette réservation et ressource.")
        tiers = [
            TariffTier(
                lower_bound=tier.lower_bound,
                upper_bound=tier.upper_bound,
                unit_price=tier.unit_price,
            )
            for tier in tariff.tiers
        ]
        return calculate_progressive_cost(amount, tiers, tariff.currency)

    def create(
        self,
        name: str,
        resource: str,
        version: int,
        currency: str,
        tiers: list[TariffTier],
    ) -> Tariff:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Le nom du tarif est obligatoire.")
        try:
            normalized_resource = Resource(resource).value
        except ValueError as exc:
            raise ValueError("Ressource inconnue.") from exc
        if version < 1:
            raise ValueError("La version du tarif doit être positive.")
        if currency.upper() != currency or len(currency) != 3:
            raise ValueError("La devise doit être un code de trois lettres majuscules.")
        calculate_progressive_cost(Decimal("0"), tiers, currency)
        return self._tariffs.create_tariff(
            normalized_name, normalized_resource, version, currency, tiers
        )

    def assign(
        self, reservation_id: UUID, resource: str, tariff_id: UUID
    ) -> None:
        try:
            normalized_resource = Resource(resource).value
        except ValueError as exc:
            raise ValueError("Ressource inconnue.") from exc
        self._tariffs.assign_to_reservation(
            reservation_id, normalized_resource, tariff_id
        )


class ReadService:
    def __init__(self, read_models: ReadModelPort) -> None:
        self._read_models = read_models

    def station_state(self, charging_point_id: str) -> ChargingPointState:
        state = self._read_models.get_charging_point_state(charging_point_id)
        if state is None:
            raise LookupError("Borne introuvable.")
        return state

    def measurements(
        self,
        charging_point_id: str,
        resource: str | None,
        session_id: UUID | None,
        offset: int,
        limit: int,
    ) -> list[Measurement]:
        self._validate_page(offset, limit)
        if resource is not None:
            try:
                resource = Resource(resource).value
            except ValueError as exc:
                raise ValueError("Ressource inconnue.") from exc
        return self._read_models.list_measurements(
            charging_point_id, resource, session_id, offset, limit
        )

    def session_consumption(self, session_id: UUID) -> list[ConsumptionTotal]:
        totals = self._read_models.get_session_consumption(session_id)
        if totals is None:
            raise LookupError("Session introuvable.")
        return totals

    def threshold_events(self, session_id: UUID) -> list[ThresholdEventRecord]:
        events = self._read_models.list_threshold_events(session_id)
        if events is None:
            raise LookupError("Session introuvable.")
        return events

    @staticmethod
    def _validate_page(offset: int, limit: int) -> None:
        if offset < 0:
            raise ValueError("offset doit être positif ou nul.")
        if not 1 <= limit <= 100:
            raise ValueError("limit doit être compris entre 1 et 100.")


class ThresholdService:
    def __init__(self, thresholds: ThresholdPort) -> None:
        self._thresholds = thresholds

    def set(
        self,
        charging_point_id: str,
        resource: str,
        alert_quantity: Decimal | None,
        cutoff_quantity: Decimal | None,
        alert_cost: Decimal | None,
        cutoff_cost: Decimal | None,
    ) -> ThresholdConfig:
        try:
            normalized_resource = Resource(resource)
        except ValueError as exc:
            raise ValueError("Ressource inconnue.") from exc
        values = (alert_quantity, cutoff_quantity, alert_cost, cutoff_cost)
        if all(value is None for value in values):
            raise ValueError("Au moins un seuil doit être configuré.")
        if any(value is not None and (not value.is_finite() or value < 0) for value in values):
            raise ValueError("Les seuils doivent être des nombres finis positifs ou nuls.")
        if (
            alert_quantity is not None
            and cutoff_quantity is not None
            and cutoff_quantity < alert_quantity
        ):
            raise ValueError("Le seuil de coupure quantité doit être supérieur au seuil d'alerte.")
        if (
            alert_cost is not None
            and cutoff_cost is not None
            and cutoff_cost < alert_cost
        ):
            raise ValueError("Le seuil de coupure coût doit être supérieur au seuil d'alerte.")
        if not charging_point_id.strip():
            raise ValueError("L'identifiant de la borne est obligatoire.")
        return self._thresholds.set_threshold(
            ThresholdConfig(
                charging_point_id=charging_point_id.strip(),
                resource=normalized_resource,
                alert_quantity=alert_quantity,
                cutoff_quantity=cutoff_quantity,
                alert_cost=alert_cost,
                cutoff_cost=cutoff_cost,
            )
        )

    def list_for_point(self, charging_point_id: str) -> list[ThresholdConfig]:
        if not charging_point_id.strip():
            raise ValueError("L'identifiant de la borne est obligatoire.")
        return self._thresholds.list_thresholds(charging_point_id)
