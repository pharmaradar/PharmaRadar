"""Authentication primitives — passwords, tokens, and the internal-call secret.

The platform holds a French pharma client's competitive intelligence and names
real clinicians, so these are the functions where a quiet regression is worst:
nothing visibly breaks when password verification starts returning True too
easily, or when a forged token is accepted.

Route-level role wiring is covered in test_route_authorization.py; this file
covers the primitives those routes are built on.
"""
import time

import jwt
import pytest

from app import auth
from app.config import get_settings


class FakeUser:
    def __init__(self, id=1, role="user", email="a@b.com", is_active=True):
        self.id, self.role, self.email, self.is_active = id, role, email, is_active


# ── Password hashing ──────────────────────────────────────

def test_a_password_verifies_against_its_own_hash():
    hashed = auth.hash_password("RocheRadar2026!")
    assert auth.verify_password("RocheRadar2026!", hashed) is True


def test_a_wrong_password_is_rejected():
    hashed = auth.hash_password("RocheRadar2026!")
    assert auth.verify_password("rocheradar2026!", hashed) is False
    assert auth.verify_password("", hashed) is False
    assert auth.verify_password("RocheRadar2026", hashed) is False


def test_the_hash_is_salted():
    """Two users with the same password must not share a hash — otherwise the
    database leaks which accounts to attack together."""
    assert auth.hash_password("same") != auth.hash_password("same")


def test_the_plaintext_never_appears_in_the_hash():
    assert "hunter2" not in auth.hash_password("hunter2")


@pytest.mark.parametrize("garbage", ["", "not-a-bcrypt-hash", "$2b$12$tooshort"])
def test_a_malformed_stored_hash_denies_access_rather_than_raising(garbage):
    """A corrupted row must fail closed. Raising here would turn a bad record
    into a 500 that leaks which account is broken."""
    assert auth.verify_password("anything", garbage) is False


def test_an_overlong_password_is_refused_with_an_actionable_error():
    """REGRESSION. bcrypt hashes at most 72 BYTES and this version raises rather
    than truncating, so a password manager generating a 100-character passphrase
    produced an opaque 500 on both user creation and password change — no field
    validated length anywhere.

    Refusing beats truncating: silently cutting to 72 bytes would make the long
    passphrase and its prefix the same password."""
    # Match OUR wording, not bcrypt's. The library's own error also contains
    # "72 bytes", so a looser assertion passed whether our guard fired or not —
    # and the point of the guard is to fail before bcrypt with a message a
    # caller can turn into a 422.
    with pytest.raises(ValueError, match="at most"):
        auth.hash_password("x" * 200)


def test_the_length_limit_is_counted_in_bytes_not_characters():
    """"é" is two bytes in UTF-8, so a 60-CHARACTER French passphrase can exceed
    a 72-BYTE limit. A character-based check would let it through to the same
    crash."""
    assert auth.password_too_long("é" * 40) is True
    assert auth.password_too_long("é" * 30) is False


def test_a_password_at_the_limit_still_works():
    at_limit = "x" * auth.BCRYPT_MAX_PASSWORD_BYTES
    assert auth.verify_password(at_limit, auth.hash_password(at_limit)) is True


def test_the_api_rejects_an_overlong_password_as_a_validation_error():
    """The boundary the client actually hits: a 422 naming the problem, not a
    500 with a traceback."""
    import pydantic
    from app.routers.auth import CreateUserBody

    with pytest.raises(pydantic.ValidationError):
        CreateUserBody(email="a@b.com", password="x" * 200)


def test_the_api_accepts_a_normal_password():
    from app.routers.auth import ChangePasswordBody

    body = ChangePasswordBody(current_password="old", new_password="RocheRadar2026!")
    assert body.new_password == "RocheRadar2026!"


# ── Tokens ────────────────────────────────────────────────

def test_a_token_round_trips_the_identity_and_role():
    token = auth.create_access_token(FakeUser(id=7, role="admin", email="x@y.com"))
    claims = auth._decode(token)
    assert claims["sub"] == "7"
    assert claims["role"] == "admin"
    assert claims["email"] == "x@y.com"


def test_a_token_carries_an_expiry():
    """Without exp a stolen token is valid forever."""
    claims = auth._decode(auth.create_access_token(FakeUser()))
    assert claims["exp"] > time.time()


def test_a_token_signed_with_another_key_is_rejected():
    """THE forgery case: anyone can mint a JWT, only our key makes it ours."""
    forged = jwt.encode({"sub": "1", "role": "superadmin"},
                        "attacker-key", algorithm="HS256")
    with pytest.raises(Exception):
        auth._decode(forged)


def test_an_expired_token_is_rejected():
    expired = jwt.encode(
        {"sub": "1", "role": "user", "exp": int(time.time()) - 60},
        get_settings().secret_key, algorithm="HS256")
    with pytest.raises(Exception):
        auth._decode(expired)


def test_an_unsigned_token_is_rejected():
    """alg=none is the classic JWT bypass."""
    unsigned = jwt.encode({"sub": "1", "role": "superadmin"}, key="", algorithm="none")
    with pytest.raises(Exception):
        auth._decode(unsigned)


def test_a_tampered_payload_is_rejected():
    """Escalating role by editing the middle segment must break the signature."""
    import base64

    token = auth.create_access_token(FakeUser(role="user"))
    header, payload, signature = token.split(".")
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    tampered = decoded.replace(b'"user"', b'"superadmin"')
    repacked = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
    with pytest.raises(Exception):
        auth._decode(f"{header}.{repacked}.{signature}")


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "not.a.token"])
def test_malformed_tokens_are_rejected(junk):
    with pytest.raises(Exception):
        auth._decode(junk)


# ── Internal service token ────────────────────────────────

def test_the_internal_token_is_stable_for_one_secret():
    """Beat calls /api/runs/trigger with this instead of a user JWT; a value
    that changed per call would break scheduled runs."""
    assert auth.internal_token() == auth.internal_token()


def test_the_internal_token_is_not_the_secret_key_itself():
    """It is derived, so leaking it in a header does not leak the signing key
    that mints admin tokens."""
    assert auth.internal_token() != get_settings().secret_key
    assert get_settings().secret_key not in auth.internal_token()


def test_the_correct_internal_token_is_accepted():
    assert auth.check_internal_token(auth.internal_token()) is True


@pytest.mark.parametrize("wrong", [None, "", "wrong", "0" * 64])
def test_a_wrong_internal_token_is_rejected(wrong):
    assert auth.check_internal_token(wrong) is False


def test_internal_token_comparison_is_constant_time():
    """hmac.compare_digest, not ==. A timing side channel on a value that
    grants admin-equivalent access is worth pinning."""
    import inspect

    assert "compare_digest" in inspect.getsource(auth.check_internal_token)
