import hashlib

import jwt
from sqlalchemy import select

from app.core import security
from app.core.config import settings
from app.core.security import create_access_token
from app.models import User

REGISTER = {"name": "Alice", "email": "Alice@Example.com", "password": "password123"}


def test_register_returns_user_without_password(client):
    res = client.post("/auth/register", json=REGISTER)

    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Alice"
    assert body["email"] == "alice@example.com"  # stored lower-case
    assert "password" not in body and "password_hash" not in body


def test_password_is_stored_hashed(client, db_session_factory):
    client.post("/auth/register", json=REGISTER)

    with db_session_factory() as db:
        user = db.scalar(select(User))
    assert user.password_hash != "password123"
    assert user.password_hash.startswith("$argon2")


def test_register_duplicate_email_any_case_is_409(client):
    client.post("/auth/register", json=REGISTER)

    res = client.post("/auth/register", json={**REGISTER, "email": "ALICE@example.COM"})

    assert res.status_code == 409


def test_register_rejects_short_password_bad_email_and_blank_name(client):
    assert client.post("/auth/register", json={**REGISTER, "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={**REGISTER, "email": "not-an-email"}).status_code == 422
    assert client.post("/auth/register", json={**REGISTER, "name": "   "}).status_code == 422


def test_login_returns_token(client):
    client.post("/auth/register", json=REGISTER)

    res = client.post("/auth/login", json={"email": "alice@example.com", "password": "password123"})

    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.jwt_expire_minutes * 60
    assert body["access_token"]


def test_login_wrong_password_and_unknown_email_give_same_401(client):
    client.post("/auth/register", json=REGISTER)

    wrong_pw = client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong-pass"})
    unknown = client.post("/auth/login", json={"email": "bob@example.com", "password": "password123"})

    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json()["detail"] == unknown.json()["detail"]


def test_me_with_token(client, make_user):
    user, headers = make_user(name="Carol")

    res = client.get("/auth/me", headers=headers)

    assert res.status_code == 200
    assert res.json() == user


def test_me_without_token_is_401(client):
    assert client.get("/auth/me").status_code == 401


def test_me_with_garbage_token_is_401(client):
    res = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


def test_me_with_expired_token_is_401(client, make_user, monkeypatch):
    user, _ = make_user()
    monkeypatch.setattr(settings, "jwt_expire_minutes", -1)
    token = create_access_token(user["id"], 0)

    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 401


def test_me_with_token_signed_by_other_secret_is_401(client, make_user):
    user, _ = make_user()
    forged = jwt.encode({"sub": str(user["id"]), "exp": 9999999999}, "a-different-secret-that-is-long-enough-123", algorithm="HS256")

    res = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})

    assert res.status_code == 401


def test_token_with_an_old_version_is_401(client, make_user, db_session_factory):
    user, headers = make_user()
    with db_session_factory() as db:
        db.get(User, user["id"]).token_version += 1
        db.commit()

    assert client.get("/auth/me", headers=headers).status_code == 401


def test_token_without_a_version_is_401(client, make_user):
    user, _ = make_user()
    old = jwt.encode({"sub": str(user["id"]), "exp": 9999999999}, settings.jwt_secret, algorithm="HS256")

    assert client.get("/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_otp_is_six_digits_and_keeps_leading_zeros(monkeypatch):
    monkeypatch.setattr(security.secrets, "randbelow", lambda _: 42)

    assert security.generate_otp() == "000042"


def test_secret_hash_is_keyed(monkeypatch):
    first = security.hash_secret("otp:1:123456")
    monkeypatch.setattr(settings, "jwt_secret", "another-secret-of-a-reasonable-length-987")

    assert security.hash_secret("otp:1:123456") != first
    assert first != hashlib.sha256(b"otp:1:123456").hexdigest()


def test_token_of_deleted_user_is_401(client, make_user, db_session_factory):
    user, headers = make_user()
    with db_session_factory() as db:
        db.delete(db.get(User, user["id"]))
        db.commit()

    assert client.get("/auth/me", headers=headers).status_code == 401
