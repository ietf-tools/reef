# Copyright The IETF Trust 2026, All Rights Reserved
"""Rendering an API response without an HTTP request.

Red's precomputer imports the website's Zod schemas so that the precomputed
file and the live response cannot describe different shapes. The same problem
here has a neater answer, because the thing being precomputed and the thing
serving it are one codebase: run the view itself. What lands in the bucket is
the byte string the API would have returned, produced by the same serializer,
renderer and permission checks, so there is no second definition to drift.

Every payload is rendered as an anonymous caller, which is the whole of what
this may publish. A key in a blob store has no reader identity attached, so
anything whose body varies by who is asking has no correct value to store; see
the exclusions in tasks.py.
"""

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

_factory = RequestFactory()


class RenderError(Exception):
    """A view answered with something other than 200."""

    def __init__(self, path, status, body):
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{path} returned HTTP {status}: {body[:200]!r}")


def render_anonymous(view, path, *, query=None, **kwargs):
    """Return the JSON bytes the API serves an anonymous GET of path.

    view is the callable from ``SomeView.as_view()``; kwargs are the URL
    captures the router would have supplied. query is a mapping, whose values
    may be lists for a repeatable parameter.
    """
    request = _factory.get(
        path,
        data=query or {},
        # Without this, content negotiation would fall through to whichever
        # renderer heads DEFAULT_RENDERER_CLASSES and could hand back a
        # browsable-API HTML page instead of the JSON the API's own callers get.
        HTTP_ACCEPT="application/json",
    )
    # No AuthenticationMiddleware runs here. DRF sets request.user from its
    # authenticators and would settle on AnonymousUser by itself; setting it is
    # for any view or permission that reaches past DRF to the underlying
    # HttpRequest.
    request.user = AnonymousUser()

    response = view(request, **kwargs)
    if hasattr(response, "render"):
        response.render()
    if response.status_code != 200:
        raise RenderError(path, response.status_code, response.content)
    return response.content
