# Copyright The IETF Trust 2026, All Rights Reserved
"""The precomputed files, rendered on request instead of read from a store.

Development only, behind REEF_SERVE_PRECOMPUTED_LIVE. Red reads these files at
``/api/v1/<key>`` on its public origin, where the worker serves them out of the
bucket. In the devcontainer there is no worker and no bucket, so this answers
the same paths, and a developer running Red against a local Reef sees the
current database without running the precomputer after every change.

A file is found by running the task that owns its key and keeping the one body
that matches, so what is served is exactly what a run would write, with no
second list of keys and views to drift from registry.py. Tasks are generators,
so the run stops at the matching key. That still costs a subject file the whole
task's setup -- a fresh fetch of Red's index and the roll-up -- which is seconds,
and acceptable for a development server.
"""

from pathlib import PurePosixPath

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_safe

from .registry import TASKS

# The Content-Type the worker serves a blob with (worker/src/blobs.ts).
CONTENT_TYPE = "application/json;charset=utf-8"


def render_key(key):
    """The bytes a precompute run writes to key, or None if no run writes it."""
    for func in TASKS.values():
        if not func.owns.match(key):
            continue
        # A per-document key is <prefix>/<doc>.json; narrowing to that document
        # is what keeps the ratings task from rendering every rated document.
        docs = {PurePosixPath(key).stem} if func.per_document else None
        for produced, body in func(docs=docs):
            if produced == key:
                return body
    return None


@require_safe
def serve(request, key):
    body = render_key(key)
    if body is None:
        raise Http404(key)
    response = HttpResponse(body, content_type=CONTENT_TYPE)
    # Rendered fresh every time, so a browser holding on to one would show a
    # developer the state before their last change.
    response["Cache-Control"] = "no-store"
    return response
