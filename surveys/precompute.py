# Copyright The IETF Trust 2026, All Rights Reserved
"""The survey list the precomputer publishes, and nothing else.

Not routed, for the same reason ``subjects.precompute`` is not: the precomputer
invokes the view directly, so the published file needs no URL, and
``reef.urls_contract`` exists so that drf-spectacular can still describe it.

What this publishes that ``surveys.api.OpenSurveyList`` does not is every
published survey whatever its visibility, so that Red can offer an
authenticated-only survey to a signed-in reader without asking the API who they
are first. Red tells the two apart by each row's ``visibility`` and offers an
``authenticated`` one only to a reader it has already signed in.

Two consequences follow from that, and neither is a gap to be closed later:

* Titles and descriptions of authenticated-only surveys are readable by anyone,
  because this file is served straight out of the blob store to whoever asks.
  What stays behind the credential is everything else -- the definition, ie the
  questions, is precomputed for open surveys alone, and responses and results
  are not precomputed at all.
* The per-caller suppression ``SurveyQuerySet.offerable_to`` applies for an
  identified caller -- dropping a survey that caller has already answered --
  cannot apply here, because a key in a blob store has no reader attached. A row
  in this file means "published, and offerable to a reader of this visibility",
  not "not yet answered". Red already suppresses a toast it has shown, so what
  this costs is a repeat offer to somebody who answered on another browser.
"""

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.permissions import AllowAny

from .models import Survey
from .serializers import OpenSurveySerializer


@extend_schema_view(
    get=extend_schema(
        # Named for the same reason the precomputed subject paths are: the shared
        # prefix would otherwise derive an id disambiguated by a numeral suffix
        # whose order nothing pins.
        operation_id="precomputed_survey_list_retrieve",
        summary="The published survey list",
        description=(
            "Not a served endpoint. This describes the payload the precomputer "
            "publishes to `surveys/published.json` in the blob store, which is "
            "where Red reads it from; no deployment routes this path.\n\n"
            "Every published survey, whatever its visibility, so that Red can "
            "decide what to offer a signed-in reader without a call to the API. "
            "`visibility` is the field that decides: offer an `authenticated` row "
            "only to a reader who is signed in, since the runner refuses an "
            "anonymous visitor and the survey's definition is not published for "
            "them either.\n\n"
            "`surveys/open.json` is the subset an anonymous reader may be offered, "
            "and is what the served `/api/reef/surveys/open/` returns without a "
            "credential. Unlike that endpoint with one, this file cannot leave out "
            "a survey the reader has already answered: it is one payload for every "
            "reader."
        ),
        responses={200: OpenSurveySerializer(many=True)},
    ),
)
class PrecomputedSurveyList(generics.ListAPIView):
    """Every published survey, rendered by the precomputer and by nothing else."""

    serializer_class = OpenSurveySerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        # Not offerable_to(): that reads request.user, which the precomputer
        # renders as anonymous, and would cut this back to the open surveys
        # surveys/open.json already holds.
        return Survey.objects.filter(status=Survey.Status.PUBLISHED).with_answered(None)
