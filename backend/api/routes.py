"""Routes HTTP v1."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.api.schemas import (
    ActivationQueued,
    ChargingSessionRead,
    ChargingPointStateRead,
    ConsumerCreate,
    ConsumerRead,
    ConsumptionTotalRead,
    CostEstimateRead,
    CostEstimateRequest,
    DeviceCommandRead,
    MeasurementPage,
    MeasurementRead,
    ReservationCreate,
    ReservationPage,
    ReservationRead,
    ReservationTariffAssignment,
    TagCreate,
    TagRead,
    TagStatusUpdate,
    SimulatedTagPresentation,
    TariffCreate,
    TariffRead,
    TierChargeRead,
    ThresholdRead,
    ThresholdEventRead,
    ThresholdUpdate,
)
from backend.application.services import (
    ActivationService,
    AccessTagService,
    ConsumerService,
    ReadService,
    ReservationService,
    TariffService,
    ThresholdService,
)
from backend.domain.entities import (
    ConflictError,
    TariffTier,
)
from backend.infrastructure.database.repositories import SqlAlchemyRepository

router = APIRouter(prefix="/api/v1")


@router.post("/consumers", response_model=ConsumerRead, status_code=status.HTTP_201_CREATED)
def create_consumer(payload: ConsumerCreate, session: Session = Depends(get_session)):
    repository = SqlAlchemyRepository(session)
    try:
        consumer = ConsumerService(repository).create(
            payload.display_name, payload.email
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return consumer


@router.post("/tags", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(payload: TagCreate, session: Session = Depends(get_session)):
    repository = SqlAlchemyRepository(session)
    service = AccessTagService(repository, repository)
    try:
        tag = service.create(payload.consumer_id, payload.code, payload.expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return tag


@router.patch("/tags/{code}", response_model=TagRead)
def set_tag_status(
    code: str, payload: TagStatusUpdate, session: Session = Depends(get_session)
):
    repository = SqlAlchemyRepository(session)
    try:
        tag = AccessTagService(repository, repository).set_status(code, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return tag


@router.post(
    "/stations/{charging_point_id}/activate",
    response_model=ActivationQueued,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_activation(
    charging_point_id: str,
    payload: SimulatedTagPresentation,
    session: Session = Depends(get_session),
):
    repository = SqlAlchemyRepository(session)
    tag_service = AccessTagService(repository, repository)
    activation_service = ActivationService(tag_service, repository)
    try:
        return activation_service.request(payload.tag_code, charging_point_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/commands/{command_id}", response_model=DeviceCommandRead)
def command_status(command_id: UUID, session: Session = Depends(get_session)):
    repository = SqlAlchemyRepository(session)
    try:
        return ActivationService(
            AccessTagService(repository, repository), repository
        ).command_status(command_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions/{session_id}", response_model=ChargingSessionRead)
def charging_session_status(
    session_id: UUID, session: Session = Depends(get_session)
):
    repository = SqlAlchemyRepository(session)
    try:
        return ActivationService(
            AccessTagService(repository, repository), repository
        ).session_status(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/sessions/{session_id}/threshold-events",
    response_model=list[ThresholdEventRead],
)
def session_threshold_events(
    session_id: UUID, session: Session = Depends(get_session)
):
    try:
        events = ReadService(SqlAlchemyRepository(session)).threshold_events(
            session_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        {
            "id": event.id,
            "session_id": event.session_id,
            "charging_point_id": event.charging_point_id,
            "resource": event.resource.value,
            "event_type": event.event_type,
            "quantity": event.quantity,
            "cost": event.cost,
            "created_at": event.created_at,
        }
        for event in events
    ]


@router.post(
    "/reservations",
    response_model=ReservationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_reservation(
    payload: ReservationCreate, session: Session = Depends(get_session)
):
    repository = SqlAlchemyRepository(session)
    try:
        reservation = ReservationService(repository).create(
            consumer_id=payload.consumer_id,
            charging_point_id=payload.charging_point_id,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return reservation


@router.get("/reservations", response_model=ReservationPage)
def list_reservations(
    charging_point_id: str = Query(min_length=1, max_length=64),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
):
    reservations = ReservationService(SqlAlchemyRepository(session)).list_for_point(
        charging_point_id, offset, limit
    )
    return ReservationPage(items=reservations, offset=offset, limit=limit)


@router.get("/reservations/{reservation_id}", response_model=ReservationRead)
def get_reservation(
    reservation_id: UUID, session: Session = Depends(get_session)
):
    repository = SqlAlchemyRepository(session)
    reservation = repository.get_reservation(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Réservation introuvable.")
    return reservation


@router.post("/tariffs", response_model=TariffRead, status_code=status.HTTP_201_CREATED)
def create_tariff(payload: TariffCreate, session: Session = Depends(get_session)):
    tiers = [
        TariffTier(
            lower_bound=tier.lower_bound,
            upper_bound=tier.upper_bound,
            unit_price=tier.unit_price,
        )
        for tier in payload.tiers
    ]
    try:
        return TariffService(SqlAlchemyRepository(session)).create(
            payload.name,
            payload.resource,
            payload.version,
            payload.currency,
            tiers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/reservations/{reservation_id}/tariffs", status_code=status.HTTP_204_NO_CONTENT)
def assign_tariff(
    reservation_id: UUID,
    payload: ReservationTariffAssignment,
    session: Session = Depends(get_session),
) -> None:
    try:
        TariffService(SqlAlchemyRepository(session)).assign(
            reservation_id, payload.resource, payload.tariff_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/reservations/{reservation_id}/cost-estimates",
    response_model=CostEstimateRead,
)
def estimate_cost(
    reservation_id: UUID,
    payload: CostEstimateRequest,
    session: Session = Depends(get_session),
):
    try:
        estimate = TariffService(SqlAlchemyRepository(session)).estimate(
            reservation_id, payload.resource, payload.quantity
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "quantity": estimate.quantity,
        "currency": estimate.currency,
        "amount": estimate.amount,
        "breakdown": [
            TierChargeRead(
                lower_bound=tier.lower_bound,
                upper_bound=tier.upper_bound,
                quantity=tier.quantity,
                unit_price=tier.unit_price,
                amount=tier.amount,
            )
            for tier in estimate.breakdown
        ],
    }


@router.get(
    "/stations/{charging_point_id}/state",
    response_model=ChargingPointStateRead,
)
def charging_point_state(
    charging_point_id: str, session: Session = Depends(get_session)
):
    try:
        return ReadService(SqlAlchemyRepository(session)).station_state(
            charging_point_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/stations/{charging_point_id}/measurements",
    response_model=MeasurementPage,
)
def measurement_history(
    charging_point_id: str,
    resource: str | None = None,
    session_id: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
):
    try:
        measurements = ReadService(SqlAlchemyRepository(session)).measurements(
            charging_point_id, resource, session_id, offset, limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MeasurementPage(
        items=[
            MeasurementRead(
                id=item.id,
                message_id=item.message_id,
                charging_point_id=item.charging_point_id,
                session_id=item.session_id,
                resource=item.resource.value,
                unit=item.unit,
                reading_type=item.reading_type,
                value=item.value,
                observed_at=item.observed_at,
            )
            for item in measurements
        ],
        offset=offset,
        limit=limit,
    )


@router.get(
    "/sessions/{session_id}/consumption",
    response_model=list[ConsumptionTotalRead],
)
def session_consumption(session_id: UUID, session: Session = Depends(get_session)):
    try:
        totals = ReadService(SqlAlchemyRepository(session)).session_consumption(
            session_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        {
            "resource": item.resource.value,
            "unit": item.unit,
            "quantity": item.quantity,
        }
        for item in totals
    ]


@router.put(
    "/stations/{charging_point_id}/thresholds/{resource}",
    response_model=ThresholdRead,
)
def set_threshold(
    charging_point_id: str,
    resource: str,
    payload: ThresholdUpdate,
    session: Session = Depends(get_session),
):
    try:
        threshold = ThresholdService(SqlAlchemyRepository(session)).set(
            charging_point_id=charging_point_id,
            resource=resource,
            alert_quantity=payload.alert_quantity,
            cutoff_quantity=payload.cutoff_quantity,
            alert_cost=payload.alert_cost,
            cutoff_cost=payload.cutoff_cost,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "charging_point_id": threshold.charging_point_id,
        "resource": threshold.resource.value,
        "alert_quantity": threshold.alert_quantity,
        "cutoff_quantity": threshold.cutoff_quantity,
        "alert_cost": threshold.alert_cost,
        "cutoff_cost": threshold.cutoff_cost,
    }


@router.get(
    "/stations/{charging_point_id}/thresholds",
    response_model=list[ThresholdRead],
)
def list_thresholds(
    charging_point_id: str, session: Session = Depends(get_session)
):
    thresholds = ThresholdService(SqlAlchemyRepository(session)).list_for_point(
        charging_point_id
    )
    return [
        {
            "charging_point_id": item.charging_point_id,
            "resource": item.resource.value,
            "alert_quantity": item.alert_quantity,
            "cutoff_quantity": item.cutoff_quantity,
            "alert_cost": item.alert_cost,
            "cutoff_cost": item.cutoff_cost,
        }
        for item in thresholds
    ]
