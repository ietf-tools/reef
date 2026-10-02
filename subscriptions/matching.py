# Copyright The IETF Trust 2026, All Rights Reserved
"""Which subscriptions a change should notify.

The query that turns changed documents into the people who asked about them,
called from the scheduled run rather than being a task.

Two halves that cannot be one query. The kinds naming a document resolve through
the document's sets and subjects. The predicate kinds say what has to have
happened rather than which document it happened to, so they are matched against
the change itself.

Both halves are batched: a run matching thousands of changes issues the same
handful of queries as one matching a single change, and resolves each change
against what those queries loaded.
"""

import logging
import operator
from collections import defaultdict
from functools import reduce

from django.db.models import Q

from docsets.models import DocumentSetEntry
from reef import rfcmeta
from reef.docids import normalize_doc_id
from subjects.tree import covering_subject_ids_by_doc

from .changes import _added, _removed, as_event
from .models import Subscription

logger = logging.getLogger("reef")

# Red's slug for a document that has been superseded or set aside.
HISTORIC_STATUS = "hist"

_RELATED = ("user", "document_set", "subject")


def _covering_documents(doc, mapping):
    """The document, plus every container it belongs to.

    A subscription naming any of them is about a change to the document.
    """
    doc = normalize_doc_id(doc)
    return [doc, *rfcmeta.containing_subseries(doc, mapping)]


def _subscriptions_naming(groups):
    """For each group of documents, the subscriptions naming any of them.

    The three kinds that name a document, for any number of groups in at most four
    queries: the subjects covering the documents (two), the sets holding them
    (one), and the subscriptions themselves (one).
    """
    every_doc = set().union(*groups)
    if not every_doc:
        return [set() for _ in groups]

    # A subject covers the documents assigned to it and to everything beneath it,
    # so a change to a document filed under dkim is news to somebody following
    # messaging. Retired subjects still cover, which covering_subject_ids_by_doc
    # preserves deliberately: a subject is staff's own and has no takedown state, so
    # there is no row here that a read has to pretend is absent.
    subjects_by_doc = covering_subject_ids_by_doc(every_doc)

    sets_by_doc = defaultdict(set)
    for doc, set_id in DocumentSetEntry.objects.filter(
        doc__in=every_doc,
        # A set staff have taken down matches nothing. The entry reaches its set
        # directly rather than through DocumentSet's manager, which is what would
        # otherwise have excluded it; a real delete would have taken the
        # subscription with it.
        document_set__deleted_at__isnull=True,
    ).values_list("doc", "document_set_id"):
        sets_by_doc[doc].add(set_id)

    set_ids = set().union(*sets_by_doc.values())
    subject_ids = set().union(*subjects_by_doc.values())
    by_rfc, by_set, by_subject = defaultdict(set), defaultdict(set), defaultdict(set)
    for subscription in Subscription.objects.filter(
        Q(kind=Subscription.Kind.RFC, params__rfc__in=every_doc)
        | Q(kind=Subscription.Kind.SET, document_set_id__in=set_ids)
        | Q(kind=Subscription.Kind.SUBJECT, subject_id__in=subject_ids)
    ).select_related(*_RELATED):
        if subscription.kind == Subscription.Kind.RFC:
            by_rfc[subscription.params.get("rfc")].add(subscription)
        elif subscription.kind == Subscription.Kind.SET:
            by_set[subscription.document_set_id].add(subscription)
        else:
            by_subject[subscription.subject_id].add(subscription)

    matched = []
    for docs in groups:
        found = set()
        for doc in docs:
            found |= by_rfc.get(doc, set())
            for set_id in sets_by_doc.get(doc, ()):
                found |= by_set.get(set_id, set())
            for subject_id in subjects_by_doc.get(doc, ()):
                found |= by_subject.get(subject_id, set())
        matched.append(found)
    return matched


def subscriptions_for_document(doc, mapping=None):
    """Subscriptions that a change to one document should notify.

    Covers the three kinds that name a document. The rfc kind holds the
    identifier in params, so it is an equality test. The set kind holds a
    foreign key, so it goes through the set's entries, against membership
    that changes underneath the subscription: someone who subscribed to a set
    last month is notified about a document added to it yesterday. The subject
    kind goes the same way through the subject's assignments, and changes
    underneath the subscription the same way: subscribing to "security" covers
    whatever carries that subject when the change lands, not what carried it
    when the subscriber signed up.

    The subject kind can be matched here at all only because the vocabulary is
    Reef's own.

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
    covers the document that changed. A parent subject is one more such container,
    expanded on the other axis: assigning a document to dkim files it under
    email-authentication, email and messaging too, and a subscriber to any of them
    named something that covers it.

    If Red cannot be reached the expansion is skipped and a bcp14 subscriber misses
    a notification they should have had; the run does not retry.

    The predicate kinds (new_rfc, by_status, obsoleted) match on what happened rather
    than on which document it happened to, and are handled by
    subscriptions_for_change.
    """
    [matched] = _subscriptions_naming([_covering_documents(doc, mapping)])
    return Subscription.objects.filter(
        pk__in=[subscription.pk for subscription in matched]
    ).select_related(*_RELATED)


def subscriptions_for_change(change, index):
    """Every subscription one change should notify, across all six kinds."""
    [matched] = match_changes([change], index)
    return matched


def _new_status(change, mapping):
    """The status a new document was published with, as by_status stores it.

    Stored stripped and lowercased by normalize_params, so compared that way. The
    parameter is Red's status name rather than its slug, which is what somebody
    subscribing through Red's UI would have picked.
    """
    if not change.is_new or mapping is None:
        return None
    status_name = (mapping.get(change.doc) or {}).get("status_name")
    return status_name.strip().lower() if status_name else None


def match_changes(changes, index):
    """Every subscription each change should notify, in the order of `changes`.

    The same queries for one change as for thousands: the document kinds through
    _subscriptions_naming, and one more for the predicate kinds.
    """
    mapping = index.mapping if index is not None else None

    groups = []
    for change in changes:
        docs = set(_covering_documents(change.doc, mapping))
        # A subseries the document has left. Joining one is already covered,
        # because the expansion is against current membership and the document is
        # in it by then; leaving one is not, because by the time the run looks the
        # document is no longer a constituent and the expansion no longer reaches
        # the people following the container. Their subseries lost a document,
        # which is news about the subseries rather than about the document.
        for departed in _departed_subseries(change):
            docs.update(_covering_documents(departed, mapping))
        groups.append(docs)
    named = _subscriptions_naming(groups)

    statuses = [_new_status(change, mapping) for change in changes]
    obsoleted = [_was_obsoleted(change) for change in changes]
    predicates = []
    if any(change.is_new for change in changes):
        predicates.append(Q(kind=Subscription.Kind.NEW_RFC))
    if wanted := {status for status in statuses if status}:
        predicates.append(
            Q(kind=Subscription.Kind.BY_STATUS, params__status__in=wanted)
        )
    if any(obsoleted):
        predicates.append(Q(kind=Subscription.Kind.OBSOLETED))

    new_rfc, by_status, on_obsolete = set(), defaultdict(set), set()
    if predicates:
        for subscription in Subscription.objects.filter(
            reduce(operator.or_, predicates)
        ).select_related(*_RELATED):
            if subscription.kind == Subscription.Kind.NEW_RFC:
                new_rfc.add(subscription)
            elif subscription.kind == Subscription.Kind.BY_STATUS:
                by_status[subscription.params.get("status")].add(subscription)
            else:
                on_obsolete.add(subscription)

    matched = []
    for change, found, status, was_obsoleted in zip(
        changes, named, statuses, obsoleted, strict=True
    ):
        found = set(found)
        if change.is_new:
            found |= new_rfc
        if status:
            found |= by_status.get(status, set())
        if was_obsoleted:
            found |= on_obsolete
        matched.append(found)
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


def plan_rfc_notifications(result):
    """Who a detection would notify, and which subscriptions matched each change.

    Per reader, not per subscription: somebody who follows a document directly and
    also holds it in a set hears once about it.

    Only reads, so the same code answers both the scheduled run and the admin
    simulation. Returns (readers by user id, [(change, event, subscriptions)]).
    """
    readers = defaultdict(lambda: {"subscriptions": set(), "events": {}})
    matches = []
    for change, subscriptions in zip(
        result.changes, match_changes(result.changes, result.index), strict=True
    ):
        event = as_event(change, result.index)
        matches.append((change, event, subscriptions))
        for subscription in subscriptions:
            reader = readers[subscription.user_id]
            reader["subscriptions"].add(subscription.pk)
            reader["events"][change.doc] = event
    return readers, matches
