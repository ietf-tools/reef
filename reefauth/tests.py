# Copyright The IETF Trust 2026, All Rights Reserved
import datetime

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.test import TestCase, override_settings
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from reefauth.authentication import BearerTokenAuthentication

_ISSUER = "https://account.ietf.org/application/o/reef/"
_AUDIENCE = "reef-client"
# Red: a second Authentik application calling the same API.
_RED_ISSUER = "https://account.ietf.org/application/o/rfc-editor/"
_RED_AUDIENCE = "rfc-editor-client"


def _make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_token(private_key, algorithm="RS256", **overrides):
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
    return jwt.encode(payload, private_key, algorithm=algorithm)


def _make_ec_key():
    return ec.generate_private_key(ec.SECP256R1())


@override_settings(
    REEF_API_OIDC_JWKS_ENDPOINTS={_ISSUER: f"{_ISSUER}jwks/"},
    REEF_API_OIDC_AUDIENCES=[_AUDIENCE],
    REEF_API_OIDC_ALGORITHMS=["RS256", "ES256"],
)
class BearerTokenAuthenticationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.key = _make_key()
        self.auth = BearerTokenAuthentication()
        # Verify against our test key instead of fetching the OP JWKS.
        self.auth.get_signing_key = lambda token, issuer: self.key.public_key()

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

    def test_wrong_audience_fails(self):
        token = _make_token(self.key, aud="some-other-client")
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)

    # The case that motivated splitting these settings from the RP login ones:
    # Red is a second Authentik application, so its tokens carry a different
    # issuer and a different `aud` from the survey runner's, and both must pass.
    @override_settings(
        REEF_API_OIDC_JWKS_ENDPOINTS={
            _ISSUER: f"{_ISSUER}jwks/",
            _RED_ISSUER: f"{_RED_ISSUER}jwks/",
        },
        REEF_API_OIDC_AUDIENCES=[_AUDIENCE, _RED_AUDIENCE],
    )
    def test_second_application_is_accepted(self):
        token = _make_token(self.key, iss=_RED_ISSUER, aud=_RED_AUDIENCE)
        request = self.factory.get(
            "/api/reef/subscriptions/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        user, payload = self.auth.authenticate(request)
        self.assertEqual(user.oidc_sub, "abc-123")
        self.assertEqual(payload["iss"], _RED_ISSUER)

    def test_unlisted_application_is_rejected(self):
        token = _make_token(self.key, iss=_RED_ISSUER, aud=_RED_AUDIENCE)
        request = self.factory.get(
            "/api/reef/subscriptions/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)

    # Red's real configuration: the rfc-editor application signs with ES256, not
    # the RS256 that Reef's own login uses. Hardcoding the RP algorithm here used
    # to reject every one of Red's tokens.
    @override_settings(
        REEF_API_OIDC_JWKS_ENDPOINTS={_RED_ISSUER: f"{_RED_ISSUER}jwks/"},
        REEF_API_OIDC_AUDIENCES=[_RED_AUDIENCE],
    )
    def test_es256_token_authenticates(self):
        ec_key = _make_ec_key()
        self.auth.get_signing_key = lambda token, issuer: ec_key.public_key()
        token = _make_token(
            ec_key, algorithm="ES256", iss=_RED_ISSUER, aud=_RED_AUDIENCE
        )
        request = self.factory.get(
            "/api/reef/subscriptions/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        user, payload = self.auth.authenticate(request)
        self.assertEqual(user.oidc_sub, "abc-123")
        self.assertEqual(payload["iss"], _RED_ISSUER)

    @override_settings(REEF_API_OIDC_ALGORITHMS=["RS256"])
    def test_algorithm_outside_the_permitted_set_is_refused(self):
        ec_key = _make_ec_key()
        self.auth.get_signing_key = lambda token, issuer: ec_key.public_key()
        token = _make_token(ec_key, algorithm="ES256")
        request = self.factory.get(
            "/api/reef/subscriptions/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)

    # Key confusion: a symmetric algorithm must never reach jwt.decode, because
    # the verifying key is the OP's published public key and anyone could sign a
    # token using it as the HMAC secret. Configuring one has to leave no permitted
    # algorithms rather than opening that hole, so every request is refused —
    # including an otherwise perfectly valid RS256 one.
    @override_settings(REEF_API_OIDC_ALGORITHMS=["HS256"])
    def test_symmetric_algorithm_is_never_permitted(self):
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.get_algorithms()

        token = _make_token(self.key)
        request = self.factory.get(
            "/api/reef/subscriptions/", HTTP_AUTHORIZATION=f"Bearer {token}"
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
