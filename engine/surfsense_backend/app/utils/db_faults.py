"""The exceptions a database call raises when the database itself fails."""

from __future__ import annotations

import asyncpg
from sqlalchemy.exc import SQLAlchemyError

# SQLAlchemy wraps driver errors raised on an open connection, but a failure
# while asyncpg connects reaches the caller raw: a refused socket is an OSError,
# "the database system is starting up" or "too many connections" is an
# asyncpg.PostgresError, and a broken protocol state is an asyncpg.InterfaceError.
DB_FAULTS: tuple[type[BaseException], ...] = (
    SQLAlchemyError,
    OSError,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
)
