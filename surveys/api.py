# Copyright The IETF Trust 2026, All Rights Reserved
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView

from .models import Survey
from .permissions import CanManageSurveys, CanViewResults
from .serializers import (
    OpenSurveySerializer,
    ResponseCreateSerializer,
    SurveyDefinitionSerializer,
    SurveySerializer,
)


class SurveyListCreate(generics.ListCreateAPIView):
    """List and create surveys. Staff only; used by the builder."""

    queryset = Survey.objects.all()
    serializer_class = SurveySerializer
    permission_classes = [CanManageSurveys]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class SurveyDetail(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update, or delete a survey. Staff only; used by the builder."""

    queryset = Survey.objects.all()
    serializer_class = SurveySerializer
    permission_classes = [CanManageSurveys]

    def perform_destroy(self, instance):
        # Withdrawn, not removed: a real delete would cascade to every response.
        instance.soft_delete()


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                name="include_authenticated",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "List an authenticated-visibility survey to an anonymous "
                    "caller too, instead of leaving it out. The runner still "
                    "refuses that survey's definition to an anonymous "
                    "visitor and prompts a login instead; this only lets "
                    "the caller see that the survey exists before that "
                    "prompt. No effect once the caller is signed in, since "
                    "they already see every visibility either way."
                ),
            ),
            OpenApiParameter(
                name="include_answered",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Keep surveys the signed-in caller has already answered, "
                    "instead of leaving them out. For a list the visitor "
                    "opened themselves; an unprompted invitation should leave "
                    "this off. No effect for an anonymous caller, whose "
                    "responses record no submitter to match on."
                ),
            ),
        ]
    )
)
class OpenSurveyList(generics.ListAPIView):
    """Open surveys Red may offer, and the surveys Reef's own list page
    offers. Bearer optional: an identified user always receives their
    targeted surveys; an anonymous caller sees open ones only, unless
    ``include_authenticated`` is set. Surveys the identified user has
    already answered are left out unless ``include_answered`` is set."""

    serializer_class = OpenSurveySerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        params = self.request.query_params
        truthy = ("1", "true", "True")
        user = self.request.user
        return Survey.objects.offerable_to(
            user,
            include_authenticated_only=params.get("include_authenticated") in truthy,
            include_answered=params.get("include_answered") in truthy,
        ).with_answered(user)


class SurveyDefinition(generics.RetrieveAPIView):
    """Definition and theme for the runner. Published surveys only; an
    authenticated-visibility survey requires a signed-in caller."""

    serializer_class = SurveyDefinitionSerializer
    permission_classes = [AllowAny]

    def get_object(self):
        survey = get_object_or_404(
            Survey, slug=self.kwargs["slug"], status=Survey.Status.PUBLISHED
        )
        if survey.requires_authentication() and not self.request.user.is_authenticated:
            raise PermissionDenied("This survey requires authentication.")
        return survey


class SurveyResponseCreate(generics.CreateAPIView):
    """Submit a response to a published survey."""

    serializer_class = ResponseCreateSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        survey = get_object_or_404(
            Survey, slug=self.kwargs["slug"], status=Survey.Status.PUBLISHED
        )
        user = self.request.user
        if survey.requires_authentication() and not user.is_authenticated:
            raise PermissionDenied("This survey requires authentication.")
        serializer.save(
            survey=survey,
            submitted_by=user if user.is_authenticated else None,
        )


class SurveyResults(APIView):
    """Aggregated results feeding the analytics dashboard. Staff only."""

    permission_classes = [CanViewResults]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request, pk):
        # all_objects: withdrawing a survey is what keeps its responses, so the
        # results have to stay readable afterwards or the keeping achieves nothing.
        survey = get_object_or_404(Survey.all_objects, pk=pk)
        results = list(survey.responses.values_list("data", flat=True))
        return DRFResponse(
            {
                "survey": {
                    "slug": survey.slug,
                    "title": survey.title,
                    "definition": survey.definition,
                },
                "count": len(results),
                "results": results,
            }
        )
