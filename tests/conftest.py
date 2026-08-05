"""Fixtures for IGLA's service-layer test suite.

Tests run against a SEPARATE database (igla_test) in the same container --
same credentials, same host, different database. A test that escapes its
transaction therefore cannot touch data you are developing against.

ISOLATION -- why join_transaction_mode matters here specifically:
IGLA's service layer commits its own transactions (see session_service.py:
create_session calls db.commit()). chat_service follows that convention.
A naive rollback fixture would not hold, because the code under test
commits before teardown runs. Binding the Session to a connection with an
already-open transaction, in join_transaction_mode="create_savepoint",
makes a service-level commit() RELEASE A SAVEPOINT instead of committing
the outer transaction -- which teardown then rolls back. Without this, the
suite silently leaks rows and tests see each other's data.

SCHEMA SOURCE: create_all from the models, NOT `alembic upgrade`. Faster
and standard, but it means this suite does not exercise the migration
chain -- a broken migration would not fail here. The migration was verified
by hand against the live schema in Task 1 instead.

KNOWN COUPLING: importing db imports config, which RAISES if
ANTHROPIC_API_KEY is absent. This suite makes zero API calls but cannot run
without that key present. Filed, not fixed -- it would block CI.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession

from db import DATABASE_URL, Base
from models import User  # noqa: F401 -- registers all tables on Base.metadata

TEST_DATABASE_URL = DATABASE_URL.set(database="igla_test")

# drop_all below is a loaded gun. This is the safety catch: if the URL is
# ever pointed anywhere but the throwaway test database, refuse to run.
if TEST_DATABASE_URL.database != "igla_test":
    raise RuntimeError(
        f"Refusing to run: test URL points at {TEST_DATABASE_URL.database!r}, "
        "not 'igla_test'."
    )


@pytest.fixture(scope="session")
def engine():
    """One engine per test session. Schema rebuilt from current models."""
    eng = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """A Session that rolls back after every test, even across commits."""
    connection = engine.connect()
    transaction = connection.begin()
    session = DbSession(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def user(db):
    """A persisted User to own conversations."""
    u = User(username="analyst_test", password_hash="not-a-real-hash")
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def other_user(db):
    """A second User. Ownership tests need someone to not be."""
    u = User(username="analyst_other", password_hash="not-a-real-hash")
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def client(db):
    """A TestClient wired to the savepoint session via dependency_overrides.

    get_db is FastAPI's own seam: overriding it routes EVERY DB access --
    every route plus require_user -- through the single connection `db` holds
    inside an open transaction. The app adds only CORSMiddleware (nothing
    DB-touching), so this one override covers all database access; there is no
    second connection to escape the savepoint, and the `db` fixture's rollback
    undoes anything a request committed.

    The override yields `db` WITHOUT closing it -- the `db` fixture owns that
    connection's teardown; closing here would collapse the savepoint mid-test.

    TestClient(app) is built WITHOUT `with`, so the app's lifespan does not
    run -- no ingest_static_docs, no ingest_generated_docs, no refresh thread.

    `app` is imported HERE, not at module scope, so the fast service-layer
    suite never pays for api.py's heavy transitive imports (ingest, rag, the
    anthropic client). Only a test that asks for `client` loads them.

    dependency_overrides.clear() in teardown resets the override after each
    test; this fixture is the sole override owner, so clearing is safe.
    """
    from fastapi.testclient import TestClient

    from api import app
    from db import get_db

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()