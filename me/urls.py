# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path("me/documents/", api.MyDocumentsList.as_view(), name="my-documents"),
]
