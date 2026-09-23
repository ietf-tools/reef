# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import views

urlpatterns = [
    path("surveys/", views.survey_list, name="manage-survey-list"),
    path("surveys/new/", views.survey_create, name="manage-survey-create"),
    path(
        "surveys/<int:pk>/status/",
        views.survey_set_status,
        name="manage-survey-status",
    ),
    path("surveys/<int:pk>/edit/", views.survey_edit, name="manage-survey-edit"),
    path(
        "surveys/<int:pk>/analytics/",
        views.survey_analytics,
        name="manage-survey-analytics",
    ),
    path("results/", views.survey_results_list, name="manage-survey-results"),
    path(
        "results/<int:pk>/",
        views.survey_responses,
        name="manage-survey-responses",
    ),
    path(
        "results/<int:pk>/export.csv",
        views.survey_responses_csv,
        name="manage-survey-responses-csv",
    ),
]
