# Copyright The IETF Trust 2026, All Rights Reserved
"""The urlconf the API contract is generated from. Nothing serves it.

`reef.urls` is what every deployment routes. This adds the views behind the
precomputed files, which are deliberately not routed anywhere: the precomputer
invokes them directly, so a published file needs no URL, and serving an
unpaginated read of the whole vocabulary would be a cost with no caller.

drf-spectacular generates from a urlconf, though, so a payload that is in no
urlconf is in no contract -- and the contract is the only description of these
files there is, now that the hand-written JSON Schema beside the precomputer is
gone. Hence a second urlconf, used by the schema command and by nothing else:

    REEF_DEPLOYMENT_MODE=build ./manage.py spectacular \\
        --urlconf reef.urls_contract --file reef_api.yaml --validate

`reef.tests_api_contract` passes the same flag, so the committed file still
cannot fall behind the code.

The paths mirror the reads each file is a cache of, under a `precomputed/`
segment that says which it is: `/api/reef/precomputed/subjects/` beside the
served `/api/reef/subjects/`. Same shape of URL, so it is obvious where a file
came from, and a distinct one, so the two entries cannot collide. Each carries a
description saying it is a published file and naming the store key.
"""

from django.urls import path

from subjects.precompute import PrecomputedSubjectDetail, SubjectIndex

from .urls import urlpatterns as served_urlpatterns

urlpatterns = [
    *served_urlpatterns,
    path(
        "api/reef/precomputed/subjects/",
        SubjectIndex.as_view(),
        name="precomputed-subject-index",
    ),
    path(
        "api/reef/precomputed/subjects/<slug:slug>/",
        PrecomputedSubjectDetail.as_view(),
        name="precomputed-subject-detail",
    ),
]
