# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path("me/ratings/", api.MyRatingList.as_view(), name="my-rating-list"),
    path("ratings/<str:rfc>/", api.RatingDetail.as_view(), name="rating-detail"),
]
