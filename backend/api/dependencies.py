"""Dépendances partagées par les routes HTTP."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


def get_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
