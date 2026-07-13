"""SQLAlchemy engine, session factory, and declarative base.

Central database wiring for IGLA's auth/user datastore. The connection
URL is built with URL.create() so the password is safely encoded rather
than hand-interpolated into a string -- special characters (@ : / #) in a
password would otherwise corrupt a raw URL. Credential pieces live in
config.py, sourced from the same .env vars the Postgres container reads:
one copy of the password, no drift.

Models import Base from here; request handlers will acquire a session
via SessionLocal.
"""

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

import config

DATABASE_URL = URL.create(
    "postgresql+psycopg",
    username=config.POSTGRES_USER,
    password=config.POSTGRES_PASSWORD,
    host=config.POSTGRES_HOST,
    port=int(config.POSTGRES_PORT),
    database=config.POSTGRES_DB,
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass