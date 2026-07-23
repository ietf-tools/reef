# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Response, Survey

User = get_user_model()


def make_survey(**kwargs):
    defaults = {
        "slug": "sat",
        "title": "Satisfaction",
        "definition": {"pages": []},
        "status": Survey.Status.PUBLISHED,
        "visibility": Survey.Visibility.OPEN,
    }
    defaults.update(kwargs)
    return Survey.objects.create(**defaults)


class OpenSurveyListTests(APITestCase):
    def setUp(self):
        make_survey(slug="open-pub", visibility=Survey.Visibility.OPEN)
        make_survey(slug="auth-pub", visibility=Survey.Visibility.AUTHENTICATED)
        make_survey(slug="draft", status=Survey.Status.DRAFT)

    def test_anonymous_sees_open_published_only(self):
        resp = self.client.get("/api/pink/surveys/open/")
        self.assertEqual(resp.status_code, 200)
        slugs = {s["slug"] for s in resp.json()}
        self.assertEqual(slugs, {"open-pub"})

    def test_authenticated_also_sees_authenticated_visibility(self):
        user = User.objects.create(username="u1", oidc_sub="sub-1")
        self.client.force_authenticate(user=user)
        resp = self.client.get("/api/pink/surveys/open/")
        slugs = {s["slug"] for s in resp.json()}
        self.assertEqual(slugs, {"open-pub", "auth-pub"})

    def test_open_item_includes_runner_url(self):
        resp = self.client.get("/api/pink/surveys/open/")
        item = resp.json()[0]
        self.assertEqual(item["url"], "/s/open-pub")


class DefinitionAndResponseTests(APITestCase):
    def test_definition_public_for_open_survey(self):
        make_survey(slug="s1")
        resp = self.client.get("/api/pink/surveys/s1/definition/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["slug"], "s1")

    def test_definition_requires_auth_for_authenticated_survey(self):
        make_survey(slug="s2", visibility=Survey.Visibility.AUTHENTICATED)
        resp = self.client.get("/api/pink/surveys/s2/definition/")
        self.assertEqual(resp.status_code, 403)

    def test_definition_404_for_draft(self):
        make_survey(slug="s3", status=Survey.Status.DRAFT)
        resp = self.client.get("/api/pink/surveys/s3/definition/")
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_can_submit_open_survey_response(self):
        make_survey(slug="s4")
        resp = self.client.post(
            "/api/pink/surveys/s4/responses/",
            {"data": {"q1": "yes"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Response.objects.count(), 1)
        stored = Response.objects.get()
        self.assertEqual(stored.data, {"q1": "yes"})
        self.assertIsNone(stored.submitted_by)

    def test_authenticated_survey_rejects_anonymous_submission(self):
        make_survey(slug="s5", visibility=Survey.Visibility.AUTHENTICATED)
        resp = self.client.post(
            "/api/pink/surveys/s5/responses/", {"data": {}}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Response.objects.count(), 0)


class ManagementPermissionTests(APITestCase):
    def test_anonymous_cannot_list_management(self):
        resp = self.client.get("/api/pink/surveys/")
        self.assertIn(resp.status_code, (401, 403))

    def test_non_staff_cannot_create(self):
        user = User.objects.create(username="u2", oidc_sub="sub-2", is_staff=False)
        self.client.force_authenticate(user=user)
        resp = self.client.post(
            "/api/pink/surveys/",
            {"slug": "new", "title": "New", "definition": {}},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_staff_can_create_and_results(self):
        staff = User.objects.create(username="admin", oidc_sub="sub-3", is_staff=True)
        self.client.force_authenticate(user=staff)
        create = self.client.post(
            "/api/pink/surveys/",
            {
                "slug": "new",
                "title": "New",
                "definition": {"pages": []},
                "status": "published",
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        survey = Survey.objects.get(slug="new")
        self.assertEqual(survey.created_by, staff)

        Response.objects.create(survey=survey, data={"q1": 1})
        results = self.client.get(f"/api/pink/surveys/{survey.id}/results/")
        self.assertEqual(results.status_code, 200)
        body = results.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"], [{"q1": 1}])
