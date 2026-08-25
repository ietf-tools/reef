# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path("subjects/", api.SubjectList.as_view(), name="subject-list"),
    path("subjects/<slug:slug>/", api.SubjectDetail.as_view(), name="subject-detail"),
]
