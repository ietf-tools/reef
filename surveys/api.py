# Copyright The IETF Trust 2026, All Rights Reserved
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
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


class OpenSurveyList(generics.ListAPIView):
    """Open surveys Red may offer. Bearer optional: an identified user also
    receives their targeted surveys, an anonymous caller sees open ones only."""

    serializer_class = OpenSurveySerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return Survey.objects.offerable_to(self.request.user)


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
        survey = get_object_or_404(Survey, pk=pk)
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
