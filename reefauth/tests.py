# Copyright The IETF Trust 2026, All Rights Reserved
import datetime

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase, override_settings
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from reefauth.authentication import BearerTokenAuthentication

_ISSUER = "https://account.ietf.org/application/o/reef/"
_AUDIENCE = "reef-client"


def _make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_token(private_key, **overrides):
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    payload = {
        "sub": "abc-123",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "name": "Ada Lovelace",
        "email": "ada@example.org",
        "iat": now,
        "exp": now + datetime.timedelta(days=365 * 100),
    }
    payload.update(overrides)
    return jwt.encode(payload, private_key, algorithm="RS256")


@override_settings(
    OIDC_OP_ISSUER_ID=_ISSUER,
    REEF_OIDC_AUDIENCE=_AUDIENCE,
    OIDC_RP_SIGN_ALGO="RS256",
)
class BearerTokenAuthenticationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.key = _make_key()
        self.auth = BearerTokenAuthentication()
        # Verify against our test key instead of fetching the OP JWKS.
        self.auth.get_signing_key = lambda token: self.key.public_key()

    def test_no_header_returns_none(self):
        request = self.factory.get("/api/reef/surveys/open/")
        self.assertIsNone(self.auth.authenticate(request))

    def test_malformed_header_fails(self):
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION="Bearer"
        )
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)

    def test_valid_token_authenticates_and_creates_user(self):
        token = _make_token(self.key)
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        user, payload = self.auth.authenticate(request)
        self.assertEqual(user.oidc_sub, "abc-123")
        self.assertEqual(user.name, "Ada Lovelace")
        self.assertEqual(user.email, "ada@example.org")
        self.assertFalse(user.is_staff)  # no staff groups configured
        self.assertEqual(payload["sub"], "abc-123")

    def test_wrong_issuer_fails(self):
        token = _make_token(self.key, iss="https://evil.example/")
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)

    @override_settings(
        REEF_OIDC_STAFF_GROUPS=["rpc-staff"], REEF_OIDC_GROUPS_CLAIM="groups"
    )
    def test_staff_group_grants_staff(self):
        token = _make_token(self.key, groups=["rpc-staff", "other"])
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        user, _ = self.auth.authenticate(request)
        self.assertTrue(user.is_staff)
