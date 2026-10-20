from collections.abc import Iterator
from contextlib import contextmanager
from sqlite3 import Connection

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(self.engine, "connect")
        def pragmas(connection: Connection, _: object) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions() as session, session.begin():
            # Serialize claims/configuration/confirmation across HTTP threads and workers.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session
