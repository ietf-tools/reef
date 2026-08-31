# Copyright The IETF Trust 2026, All Rights Reserved
"""Where a survey is offered, and who has already answered it."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APITestCase

from subjects.models import Subject, SubjectAssignment
from surveys.audience import resolve_audience, validate_audience
from surveys.models import Response, Survey

User = get_user_model()


def published(**overrides):
    return Survey.objects.create(
        **{
            "title": "How is it going",
            "slug": "how",
            "status": Survey.Status.PUBLISHED,
            "visibility": Survey.Visibility.OPEN,
            **overrides,
        }
    )


class ValidateAudienceTests(TestCase):
    def test_no_audience_is_fine(self):
        validate_audience(None)

    def test_a_misspelt_key_is_refused(self):
        """It would otherwise target nothing while looking targeted."""
        with self.assertRaises(ValidationError):
            validate_audience({"document": ["rfc9110"]})

    def test_a_non_object_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_audience(["rfc9110"])

    def test_a_non_list_value_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_audience({"documents": "rfc9110"})

    def test_an_unparseable_identifier_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_audience({"documents": ["not-a-document"]})

    def test_the_model_validates_and_canonicalises_on_save(self):
        survey = published(audience={"documents": ["RFC 9110"]})
        survey.refresh_from_db()
        self.assertEqual(survey.audience["documents"], ["rfc9110"])

    def test_the_model_refuses_a_bad_audience_outside_the_api(self):
        with self.assertRaises(ValidationError):
            published(audience={"nonsense": []})


class ResolveAudienceTests(TestCase):
    def test_no_audience_means_everywhere(self):
        self.assertIsNone(resolve_audience(None))
        self.assertIsNone(resolve_audience({}))

    def test_explicit_documents_resolve_to_themselves(self):
        self.assertEqual(
            resolve_audience({"documents": ["rfc9110", "bcp14"]}), ["bcp14", "rfc9110"]
        )

    def test_a_subject_resolves_to_the_documents_carrying_it(self):
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        SubjectAssignment.objects.create(subject=subject, doc="rfc8446")
        self.assertEqual(
            resolve_audience({"subjects": ["security"]}), ["rfc8446", "rfc9110"]
        )

    def test_documents_and_subjects_are_unioned_without_duplicates(self):
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        self.assertEqual(
            resolve_audience({"documents": ["rfc9110"], "subjects": ["security"]}),
            ["rfc9110"],
        )

    def test_a_subject_with_no_documents_targets_nothing_not_everything(self):
        """The distinction null and [] exist to carry. A survey aimed at an empty
        subject must appear nowhere."""
        Subject.objects.create(name="Security", slug="security")
        self.assertEqual(resolve_audience({"subjects": ["security"]}), [])

    def test_a_subject_that_does_not_exist_contributes_nothing(self):
        self.assertEqual(resolve_audience({"subjects": ["nope"]}), [])

    def test_resolution_follows_the_vocabulary_rather_than_the_saved_value(self):
        """A document assigned to the subject later falls inside the audience without
        anybody reopening the survey."""
        subject = Subject.objects.create(name="Security", slug="security")
        audience = {"subjects": ["security"]}
        self.assertEqual(resolve_audience(audience), [])
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        self.assertEqual(resolve_audience(audience), ["rfc9110"])

    def test_an_unparseable_identifier_does_not_cost_the_rest(self):
        """Saved before validation existed, or edited around it."""
        with self.assertLogs("reef", level="WARNING"):
            resolved = resolve_audience({"documents": ["rfc9110", "rubbish"]})
        self.assertEqual(resolved, ["rfc9110"])

    def test_the_result_is_sorted(self):
        """It is published in a precomputed file, which has to be the same bytes when
        nothing has changed."""
        self.assertEqual(
            resolve_audience({"documents": ["rfc9110", "rfc8446", "bcp14"]}),
            ["bcp14", "rfc8446", "rfc9110"],
        )


class OpenSurveyListTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="u", oidc_sub="s")

    def open_surveys(self):
        response = self.client.get("/api/reef/surveys/open/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_an_untargeted_survey_carries_null_documents(self):
        published()
        self.assertIsNone(self.open_surveys()[0]["documents"])

    def test_a_targeted_survey_carries_its_documents(self):
        published(audience={"documents": ["rfc9110"]})
        self.assertEqual(self.open_surveys()[0]["documents"], ["rfc9110"])

    def test_a_survey_the_caller_answered_is_not_offered_again(self):
        """Only Reef can know this: the visitor submits on Reef's runner, on a
        different origin from Red."""
        survey = published()
        Response.objects.create(survey=survey, data={}, submitted_by=self.user)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.open_surveys(), [])

    def test_somebody_else_having_answered_does_not_hide_it(self):
        survey = published()
        other = User.objects.create(username="o", oidc_sub="o")
        Response.objects.create(survey=survey, data={}, submitted_by=other)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(len(self.open_surveys()), 1)

    def test_an_anonymous_response_hides_it_from_nobody(self):
        """It records no submitter, on purpose, so there is nothing to match on."""
        survey = published()
        Response.objects.create(survey=survey, data={}, submitted_by=None)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(len(self.open_surveys()), 1)

    def test_an_anonymous_caller_still_sees_a_survey_others_have_answered(self):
        survey = published()
        Response.objects.create(survey=survey, data={}, submitted_by=self.user)
        self.assertEqual(len(self.open_surveys()), 1)

    def test_targeting_does_not_change_who_may_take_a_survey(self):
        """It decides where a survey is shown, not who it is for."""
        published(
            visibility=Survey.Visibility.AUTHENTICATED,
            audience={"documents": ["rfc9110"]},
        )
        self.assertEqual(self.open_surveys(), [])
        self.client.force_authenticate(user=self.user)
        self.assertEqual(len(self.open_surveys()), 1)


class ManageApiAudienceTests(APITestCase):
    """The builder must get a 400 for a typo, not a 500.

    The model validates every write path, but it raises Django's ValidationError,
    which DRF does not translate; without validation at the serializer the manage API
    would return a server error for a field it exists to let staff edit.
    """

    def setUp(self):
        self.user = User.objects.create(
            username="staff", oidc_sub="staff", is_staff=True, is_superuser=True
        )
        self.client.force_authenticate(user=self.user)

    def post(self, audience):
        return self.client.post(
            "/api/reef/surveys/",
            {
                "slug": "s",
                "title": "S",
                "definition": {},
                "status": "draft",
                "visibility": "open",
                "audience": audience,
            },
            format="json",
        )

    def test_a_misspelt_key_is_a_bad_request(self):
        response = self.post({"document": ["rfc9110"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("audience", response.json())

    def test_an_unparseable_identifier_is_a_bad_request(self):
        self.assertEqual(self.post({"documents": ["nonsense"]}).status_code, 400)

    def test_a_good_audience_is_accepted_and_canonicalised(self):
        response = self.post({"documents": ["RFC 9110"]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["audience"]["documents"], ["rfc9110"])

    def test_no_audience_is_accepted(self):
        self.assertEqual(self.post(None).status_code, 201)
