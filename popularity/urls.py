# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path("popularity/", api.PopularityList.as_view(), name="popularity-list"),
]
