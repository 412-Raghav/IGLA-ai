"""Authentication routes: register, login, logout.

Grouped as an APIRouter and included into the app in api.py. Login sets
an opaque session id as an HttpOnly cookie; the browser returns it on
each request and the session dependency (used by protected routes)
reads it from there -- the client never handles a token by hand.

Passwords are bounded at bcrypt's 72-byte limit at THIS boundary (the
Pydantic model), so an over-long password is a clean 422, not a 500
deep in the hashing layer.
"""

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session as DbSession

from db import get_db
from models import User
from security import MAX_PASSWORD_BYTES, hash_password, verify_password
from session_service import create_session, delete_session

logger = logging.getLogger("igla")

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "igla_session"


class Credentials(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("username must not be empty")
        if len(cleaned) > 64:
            raise ValueError("username exceeds 64 characters")
        return cleaned

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        """Enforce bcrypt's 72-BYTE ceiling at the API boundary.

        bcrypt hashes at most 72 bytes; multibyte UTF-8 chars count for
        more than one. Rejecting here yields a clean 422 instead of a
        ValueError surfacing as a 500 in the hashing layer.
        """
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"password exceeds {MAX_PASSWORD_BYTES}-byte limit"
            )
        return value


@router.post("/register", status_code=201)
def register(creds: Credentials, db: DbSession = Depends(get_db)):
    """Create a new account. 409 if the username is taken."""
    existing = db.query(User).filter(User.username == creds.username).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        username=creds.username,
        password_hash=hash_password(creds.password),
    )
    db.add(user)
    db.commit()
    logger.info("Registered new user id=%s", user.id)
    return {"id": user.id, "username": user.username}


@router.post("/login")
def login(
    creds: Credentials,
    response: Response,
    db: DbSession = Depends(get_db),
):
    """Verify credentials, mint a session, set it as an HttpOnly cookie."""
    user = db.query(User).filter(User.username == creds.username).first()
    if user is None or not verify_password(creds.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = create_session(user.id, db)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    logger.info("Login succeeded for user id=%s", user.id)
    return {"status": "logged in", "username": user.username}


@router.post("/logout")
def logout(
    response: Response,
    igla_session: str | None = Cookie(default=None),
    db: DbSession = Depends(get_db),
):
    """Delete the current session and clear the cookie. Idempotent."""
    if igla_session is not None:
        delete_session(igla_session, db)
    response.delete_cookie(key=SESSION_COOKIE)
    return {"status": "logged out"}