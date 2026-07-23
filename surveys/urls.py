# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

app_name = "surveys"

urlpatterns = [
    path("surveys/", api.SurveyListCreate.as_view(), name="survey-list"),
    path("surveys/open/", api.OpenSurveyList.as_view(), name="survey-open"),
    path("surveys/<int:pk>/", api.SurveyDetail.as_view(), name="survey-detail"),
    path(
        "surveys/<int:pk>/results/",
        api.SurveyResults.as_view(),
        name="survey-results",
    ),
    path(
        "surveys/<slug:slug>/definition/",
        api.SurveyDefinition.as_view(),
        name="survey-definition",
    ),
    path(
        "surveys/<slug:slug>/responses/",
        api.SurveyResponseCreate.as_view(),
        name="survey-responses",
    ),
]
