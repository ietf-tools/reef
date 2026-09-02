# Copyright The IETF Trust 2026, All Rights Reserved
"""Roll-up over the subject tree, in one place because four callers need it.

A subject covers the documents assigned to it and to everything beneath it.
Assigning smtp puts that document under email and under messaging too, which is
what makes a branch with no assignments of its own worth having: without it,
messaging is a heading that matches nothing, a subscription to it is dead the day
it is made, and its page in Red says there is nothing on the subject of messaging.

Four places asked the same question of the flat vocabulary with the same one-hop
join -- subscriptions/matching.py, stats/api.py, surveys/audience.py and
subjects/serializers.py -- and the risk in making the vocabulary a tree was never
the SQL. It was that four independent joins would be updated one at a time. So
they all come through here.

Nothing in this module recurses in SQL. Ancestors are the prefixes of a path, so
they are read off the string; descendants are one indexed prefix match. That
holds at any depth, and the four-level ceiling is not what makes it work.
"""

from collections import defaultdict

from .models import Subject, ancestor_paths

# Re-exported so that a caller doing roll-up has one module to import from, even
# though the definition lives beside the path column it reads.
__all__ = [
    "ancestor_paths",
    "covering_subject_ids",
    "documents_under",
    "rollup",
]


def covering_subject_ids(docs):
    """The ids of every subject that covers any of these documents.

    Covering means assigned-or-above: a subject with one of the documents on it,
    and every subject that one sits under. This is what a subscription match tests
    against, and it is two queries whatever the shape of the tree -- the paths of
    the subjects holding the documents, and then the ids of those paths' prefixes.

    all_objects, because a retired subject goes on matching for the readers who
    already follow it. That is the whole point of retiring rather than deleting,
    and it is the rule subscriptions/matching.py already relies on by reaching
    Subscription.subject through the base manager.
    """
    assigned = set(
        Subject.all_objects.filter(assignments__doc__in=docs).values_list(
            "path", flat=True
        )
    )
    if not assigned:
        return set()
    wanted = set(assigned)
    for path in assigned:
        wanted.update(ancestor_paths(path))
    return set(Subject.all_objects.filter(path__in=wanted).values_list("pk", flat=True))


def documents_under(subject):
    """Every document the subject covers, deduplicated, in identifier order.

    One query over the subtree. A document assigned to two subjects in the same
    branch -- to smtp and to email both -- counts once.
    """
    from .models import SubjectAssignment

    docs = set(
        SubjectAssignment.objects.filter(
            subject__in=Subject.all_objects.at_or_under(subject)
        ).values_list("doc", flat=True)
    )
    return sorted(docs)


def rollup():
    """The whole vocabulary's assignments and coverage, in one pass.

    What the precomputer needs and what a per-subject query would make quadratic:
    six hundred subtree queries before it started on the metadata. Two queries
    instead, and then every subject's direct documents, covered documents and both
    counts fall out of adding each assignment to its own subject and to each of its
    ancestors.

    Returns (direct, covered), both keyed by subject path. Sorted lists rather
    than sets, because the caller publishes them and a precomputed file has to be
    byte-stable between runs that found the same data.
    """
    from .models import SubjectAssignment

    paths = dict(Subject.all_objects.values_list("pk", "path"))
    direct = defaultdict(set)
    covered = defaultdict(set)
    for path in paths.values():
        direct[path], covered[path] = set(), set()

    pairs = SubjectAssignment.objects.values_list("subject_id", "doc")
    for subject_id, doc in pairs:
        path = paths.get(subject_id)
        if path is None:
            continue
        direct[path].add(doc)
        covered[path].add(doc)
        for ancestor in ancestor_paths(path):
            covered[ancestor].add(doc)

    order = _doc_sort_key
    return (
        {path: sorted(docs, key=order) for path, docs in direct.items()},
        {path: sorted(docs, key=order) for path, docs in covered.items()},
    )


def _doc_sort_key(doc):
    """Series first, then number, so rfc9 sorts before rfc10.

    Lexical order would put rfc10 before rfc9, which is not wrong so much as
    obviously unconsidered in a published file a person reads.
    """
    for index, char in enumerate(doc):
        if char.isdigit():
            return (doc[:index], int(doc[index:]))
    return (doc, 0)
