from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from lucius.persistence.orm import Base


def create_sqlite_engine(database_path: str | Path = ":memory:") -> Engine:
    raw_str = str(database_path).strip()
    if raw_str == ":memory:":
        url = "sqlite+pysqlite:///:memory:"
    else:
        for prefix in ("sqlite+pysqlite:///", "sqlite+pysqlite://", "sqlite:///", "sqlite://", "sqlite:/", "sqlite:"):
            if raw_str.startswith(prefix):
                raw_str = raw_str[len(prefix):]
                break

        path = Path(raw_str).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+pysqlite:///{path}"
    return create_engine(url, connect_args={"timeout": 10.0}, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def create_all(engine: Engine) -> None:
    Base.metadata.create_all(engine)
