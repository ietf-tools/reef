# Copyright The IETF Trust 2026, All Rights Reserved
import datetime

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from reefauth.authentication import BearerTokenAuthentication
from reefauth.backends import ReefOIDCAuthBackend
from reefauth.checks import cors_origins_configured
from reefauth.models import User

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

    @override_settings(REEF_OIDC_HOST="https://account.ietf.org")
    def test_an_unlisted_application_is_named_by_its_slug(self):
        token = _make_token(self.key, iss=_RED_ISSUER)
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        with self.assertRaisesMessage(
            exceptions.AuthenticationFailed,
            "slug is 'rfc-editor'; add that to REEF_API_OIDC_APP_SLUGS, which "
            "currently names 'reef'.",
        ):
            self.auth.authenticate(request)

    def test_wrong_audience_fails(self):
        token = _make_token(self.key, aud="some-other-client")
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)

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
    # the RS256 that Reef's own login uses.
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
        REEF_OIDC_STAFF_GROUPS=["rpc-staff"], REEF_OIDC_SUPERUSER_GROUPS=["team-dev"]
    )
    def test_groups_in_a_bearer_token_grant_nothing(self):
        token = _make_token(self.key, groups=["rpc-staff", "team-dev"])
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        user, _ = self.auth.authenticate(request)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    # An admin signed in through reef-admin who then uses the survey runner or
    # Red: those tokens carry no staff groups, and must not demote the shared
    # User row out of the admin.
    def test_a_bearer_token_keeps_an_admins_permissions(self):
        User.objects.create(
            username="authentik-abc-123",
            oidc_sub="abc-123",
            name="Old Name",
            is_staff=True,
            is_superuser=True,
        )
        token = _make_token(self.key)
        request = self.factory.get(
            "/api/reef/surveys/open/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.auth.authenticate(request)
        user = User.objects.get(oidc_sub="abc-123")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.name, "Ada Lovelace")  # profile fields still sync


class OIDCLoginBackendPermissionTests(TestCase):
    """The reef-admin login is what decides is_staff and is_superuser."""

    def setUp(self):
        self.backend = ReefOIDCAuthBackend()

    @override_settings(
        REEF_OIDC_STAFF_GROUPS=["rpc-staff"], REEF_OIDC_GROUPS_CLAIM="groups"
    )
    def test_staff_group_grants_staff(self):
        user = self.backend.create_user(
            {"sub": "abc-123", "groups": ["rpc-staff", "other"]}
        )
        self.assertTrue(user.is_staff)

    @override_settings(REEF_OIDC_SUPERUSER_GROUPS=["team-dev"])
    def test_superuser_group_grants_superuser_and_staff(self):
        user = self.backend.create_user({"sub": "abc-123", "groups": ["team-dev"]})
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)  # the admin refuses a superuser who isn't

    @override_settings(REEF_OIDC_STAFF_GROUPS=[], REEF_OIDC_SUPERUSER_GROUPS=[])
    def test_no_superuser_groups_configured_grants_neither(self):
        user = self.backend.create_user({"sub": "abc-123", "groups": ["team-dev"]})
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)

    @override_settings(REEF_OIDC_STAFF_GROUPS=["rpc-staff"])
    def test_leaving_the_staff_group_revokes_staff_at_next_login(self):
        user = self.backend.create_user({"sub": "abc-123", "groups": ["rpc-staff"]})
        user = self.backend.update_user(user, {"sub": "abc-123", "groups": []})
        self.assertFalse(user.is_staff)


class CorsOriginsCheckTests(SimpleTestCase):
    @override_settings(DEPLOYMENT_MODE="staging", CORS_ALLOWED_ORIGINS=[])
    def test_a_cross_origin_deployment_with_no_origins_warns(self):
        [warning] = cors_origins_configured(None)
        self.assertEqual(warning.id, "reefauth.W001")

    @override_settings(
        DEPLOYMENT_MODE="production",
        CORS_ALLOWED_ORIGINS=["https://www.rfc-editor.org"],
    )
    def test_a_configured_deployment_is_quiet(self):
        self.assertEqual(cors_origins_configured(None), [])

    @override_settings(DEPLOYMENT_MODE="development", CORS_ALLOWED_ORIGINS=[])
    def test_development_is_not_asked(self):
        self.assertEqual(cors_origins_configured(None), [])


class UserDisplayNameTests(SimpleTestCase):
    """get_username()/__str__ are what the admin login page and the admin's
    "Welcome, ..." banner show — the opaque authentik-<sub> username is only
    a fallback when no better claim was available."""

    def test_prefers_name(self):
        user = User(
            username="authentik-abc", name="Ada Lovelace", email="ada@example.org"
        )
        self.assertEqual(user.get_username(), "Ada Lovelace")
        self.assertEqual(str(user), "Ada Lovelace")

    def test_falls_back_to_email_without_a_name(self):
        user = User(username="authentik-abc", email="ada@example.org")
        self.assertEqual(user.get_username(), "ada@example.org")

    def test_falls_back_to_username_without_name_or_email(self):
        user = User(username="authentik-abc")
        self.assertEqual(user.get_username(), "authentik-abc")


class AdminLoginPageTests(TestCase):
    """/admin/login/ must still offer the break-glass username/password form
    (for when Authentik is unavailable) alongside the Authentik link, since
    that form is the only way in for the local superuser."""

    def test_login_page_offers_both_authentik_and_the_local_form(self):
        response = self.client.get("/admin/login/")
        oidc_url = reverse("oidc_authentication_init")
        self.assertContains(response, f'href="{oidc_url}')
        self.assertContains(response, 'name="password"')
