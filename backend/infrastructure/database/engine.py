"""Construction de l'engine et des sessions SQLAlchemy."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from backend.settings import Settings


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        connect_args={"connect_timeout": 5},
        pool_pre_ping=True,
        pool_timeout=5,
    )


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
