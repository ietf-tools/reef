# Copyright The IETF Trust 2026, All Rights Reserved
"""Which subscriptions a change should notify.

Split out of tasks.py because none of it is a task: it is the query that turns one
changed document into the people who asked about it, and it is called from the
scheduled run rather than being one.

Two halves that cannot be one query, for a reason the model has carried since it was
written. The kinds naming a document resolve by join. The predicate kinds say what has
to have happened rather than which document it happened to, so they are matched
against the change itself.
"""

import logging

from django.db.models import Q

from reef import rfcmeta
from reef.docids import normalize_doc_id

from .changes import _added, _removed
from .models import Subscription

logger = logging.getLogger("reef")

# Red's slug for a document that has been superseded or set aside.
HISTORIC_STATUS = "hist"


def subscriptions_for_document(doc):
    """Subscriptions that a change to one document should notify.

    Covers the three kinds that name a document. The rfc kind holds the
    identifier in params, so it is an equality test. The set kind holds a
    foreign key, so it is a join through the set's entries, against membership
    that changes underneath the subscription: someone who subscribed to a set
    last month is notified about a document added to it yesterday. The subject
    kind is the same shape of join, through the subject's assignments, and
    changes underneath the subscription the same way: subscribing to
    "security" covers whatever carries that subject when the change lands, not
    what carried it when the subscriber signed up.

    The subject kind can be matched here at all only because the vocabulary is
    Reef's own. It was drafted as a predicate over the event, alongside the
    kinds below, back when a subject was going to arrive on the event from the
    datatracker; hosting the vocabulary here turned it into a join and moved
    it off the ingest path's critical list.

    Subseries are expanded, so a change to rfc2119 matches a subscription to bcp14,
    which is what somebody subscribing to BCP 14 meant. The membership comes from
    Red's published index through reef.rfcmeta rather than from a Reef table, because
    it changes over time and Reef holds no document state to keep in step: BCP 14 is
    currently RFC 2119 and RFC 8174 and has not always been. Matching against what
    Red says today is the point, in the same way that a set subscription matches
    membership as it stands when the change lands rather than when somebody
    subscribed.

    All three kinds expand alike: a set holding bcp14 and a subject assigned to bcp14
    both match a change to rfc2119, because in each case what the subscriber named
    covers the document that changed.

    If Red cannot be reached the expansion is skipped, and a bcp14 subscriber misses
    a notification they should have had. That is a real gap rather than a tidy
    degradation; it is left here because the retry that would fix it belongs to the
    ingest path, which does not exist yet. See the subseries open item in plan.md.

    The predicate kinds (new_rfc, by_status, obsoleted) match on what happened rather
    than on which document it happened to, so they are not here; they belong to the
    ingest path once the event shape is known.
    """
    doc = normalize_doc_id(doc)
    # The changed document, plus every container it belongs to. A subscription naming
    # any of them is about this change.
    docs = [doc, *rfcmeta.containing_subseries(doc)]
    return (
        Subscription.objects.filter(
            Q(kind=Subscription.Kind.RFC, params__rfc__in=docs)
            # A set staff have taken down matches nothing. The join reaches the
            # rows directly and so does not go through DocumentSet's manager,
            # which is what would otherwise have excluded them; a real delete
            # would have taken the subscription with it.
            | Q(
                kind=Subscription.Kind.SET,
                document_set__entries__doc__in=docs,
                document_set__deleted_at__isnull=True,
            )
            # No equivalent takedown filter for subjects: a subject is staff's
            # own and has no state between existing and not, so there is no
            # row here that a read has to pretend is absent.
            | Q(kind=Subscription.Kind.SUBJECT, subject__assignments__doc__in=docs)
        )
        .distinct()
        .select_related("user", "document_set", "subject")
    )


def subscriptions_for_change(change, index):
    """Every subscription one change should notify, across all six kinds.

    Two halves that cannot be one query. The kinds naming a document resolve by join,
    through subscriptions_for_document, which also expands the subseries containing
    it. The predicate kinds say what has to have happened rather than which document
    it happened to, so they are matched against the change itself; the model has said
    so since it was written, and this is the code it was waiting for.
    """
    matched = set(subscriptions_for_document(change.doc))

    # A subseries the document has left. Joining one is already covered, because
    # subscriptions_for_document expands against current membership and the document
    # is in it by then; leaving one is not, because by the time the run looks the
    # document is no longer a constituent and the expansion no longer reaches the
    # people following the container. Their subseries lost a document, which is news
    # about the subseries rather than about the document, and until the snapshot
    # started holding the previous membership there was no way to know it happened.
    for departed in _departed_subseries(change):
        matched |= set(subscriptions_for_document(departed))

    meta = (index.mapping.get(change.doc) or {}) if index is not None else {}
    predicates = Q(pk__in=[])  # matches nothing, so the ors below need no condition

    if change.is_new:
        predicates |= Q(kind=Subscription.Kind.NEW_RFC)
        status_name = meta.get("status_name")
        if status_name:
            # Stored stripped and lowercased by normalize_params, so compared that
            # way. The parameter is Red's status name rather than its slug, which is
            # what somebody subscribing through Red's UI would have picked.
            predicates |= Q(
                kind=Subscription.Kind.BY_STATUS,
                params__status=status_name.strip().lower(),
            )

    if _was_obsoleted(change):
        predicates |= Q(kind=Subscription.Kind.OBSOLETED)

    matched |= set(
        Subscription.objects.filter(predicates).select_related(
            "user", "document_set", "subject"
        )
    )
    return matched


def _departed_subseries(change):
    """The subseries this change took the document out of."""
    if change.is_new or "subseries" not in change.fields:
        return []
    return _removed(change.fields["subseries"])


def _was_obsoleted(change):
    """Whether a change is the document being obsoleted or made historic.

    Both, because the obsoleted kind offers them together: a document is usually made
    historic by the thing that obsoletes it, and occasionally without one.
    """
    if change.is_new:
        return False
    if "obsoleted_by" in change.fields and _added(change.fields["obsoleted_by"]):
        return True
    if "status" in change.fields:
        return change.fields["status"][1] == HISTORIC_STATUS
    return False
