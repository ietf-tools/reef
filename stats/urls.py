# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path("stats/", api.DocumentStatsList.as_view(), name="document-stats"),
]
